"""Question-answering API for the permission-aware RAG MVP."""
from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from app.auth import (
    InvalidContext,
    NeedsContextClarification,
    UnknownPersona,
    authenticate_user,
    change_password,
    context_unit,
    get_persona,
    list_personas,
    resolve_context,
    unit_name,
)
from app.conversations import append_exchange, get_conversation, list_conversations
from app.rag.pipeline import AskResponse, ContextOption, RequestContext

router = APIRouter(prefix="/api/v1", tags=["query"])


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    context_id: str | None = None
    as_of: date | None = None
    conversation_id: str | None = None

class LoginRequest(BaseModel):
    username_or_email: str
    password: str = ""

class ChangePasswordRequest(BaseModel):
    username_or_email: str
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


def _container(request: Request) -> Any:
    container = getattr(request.app.state, "container", None)
    if container is None:
        raise HTTPException(status_code=503, detail="Ứng dụng chưa sẵn sàng")
    return container


def _persona(persona_id: str | None):
    if not persona_id:
        raise HTTPException(status_code=401, detail="Yêu cầu đăng nhập")
    try:
        return get_persona(persona_id)
    except UnknownPersona as exc:
        raise HTTPException(status_code=401, detail="Tài khoản không tồn tại hoặc đã bị vô hiệu hóa") from exc


def _serialize_persona(p: Any) -> dict[str, Any]:
    contexts = []
    for ctx in p.allowed_contexts:
        unit_id = context_unit(ctx)
        contexts.append({"id": ctx, "label": unit_name(unit_id)})
    return {
        "id": p.user_id,
        "name": p.name,
        "email": p.email,
        "title": p.title,
        "role": p.role,
        "clearance": p.clearance,
        "description": p.description,
        "contexts": contexts,
        "default_context_id": p.allowed_contexts[0] if p.allowed_contexts else None,
        "is_persona": p.is_persona,
    }


@router.get("/personas")
def get_all_personas() -> list[dict[str, Any]]:
    """Return active users and memberships stored in PostgreSQL."""
    return [_serialize_persona(p) for p in list_personas()]


@router.post("/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    """Authenticate using the password hash stored for the database user."""
    p = authenticate_user(payload.username_or_email, payload.password)
    if not p:
        raise HTTPException(
            status_code=401,
            detail="Tài khoản hoặc mật khẩu không chính xác.",
        )
    return {
        "status": "success",
        "user": _serialize_persona(p),
    }


@router.post("/auth/change-password")
def change_user_password(payload: ChangePasswordRequest) -> dict[str, str]:
    """Change the account password after verifying its current password."""
    if not change_password(
        payload.username_or_email,
        payload.current_password,
        payload.new_password,
    ):
        raise HTTPException(status_code=401, detail="Thông tin xác thực không chính xác.")
    return {"status": "success", "message": "Đã đổi mật khẩu."}


@router.get("/conversations")
def conversations(
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
) -> list[dict[str, Any]]:
    p = _persona(x_persona_id)
    return list_conversations(p.user_id)


@router.get("/conversations/{conversation_id}")
def conversation(
    conversation_id: str,
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
) -> dict[str, Any]:
    p = _persona(x_persona_id)
    result = get_conversation(p.user_id, conversation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")
    return result


@router.get("/me")
def me(x_persona_id: str | None = Header(default=None, alias="X-Persona-Id")) -> dict[str, Any]:
    p = _persona(x_persona_id)
    contexts = []
    for ctx in p.allowed_contexts:
        unit_id = context_unit(ctx)
        contexts.append({"id": ctx, "label": unit_name(unit_id)})
    return {
        "id": p.user_id,
        "name": p.name,
        "email": p.email,
        "title": p.title,
        "clearance": p.clearance,
        "role": p.role,
        "contexts": contexts,
        "personas": [_serialize_persona(item) for item in list_personas()],
    }


@router.post("/ask")
def ask(
    payload: AskRequest,
    request: Request,
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
):
    p = _persona(x_persona_id)
    try:
        context_id = resolve_context(p, payload.context_id)
    except NeedsContextClarification as exc:
        contexts = [
            ContextOption(id=ctx, label=unit_name(context_unit(ctx)))
            for ctx in exc.available_contexts
        ]
        return AskResponse(
            status="needs_clarification",
            answer="Bạn đang có nhiều ngữ cảnh. Vui lòng chọn ngữ cảnh trước khi hỏi.",
            available_contexts=contexts,
            request_id=f"req_{uuid.uuid4().hex[:12]}",
        )
    except InvalidContext as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    conversation_id = payload.conversation_id or str(uuid.uuid4())
    if payload.conversation_id and get_conversation(p.user_id, conversation_id) is None:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện")

    pipeline = _container(request).pipeline
    response = pipeline.ask(
        ctx=RequestContext(user_id=p.user_id, context_id=context_id, clearance=p.clearance),
        question=payload.question,
        as_of=payload.as_of,
    )
    response.conversation_id = conversation_id
    append_exchange(
        user_id=p.user_id,
        conversation_id=conversation_id,
        context_id=context_id,
        question=payload.question,
        answer=response.model_dump(mode="json"),
    )
    return response
