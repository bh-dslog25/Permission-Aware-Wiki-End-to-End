"""Audit HTTP endpoints."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Header, HTTPException, Query

from app.audit import query_audit_logs
from app.auth import UnknownPersona, get_persona

router = APIRouter(prefix="/api/v1", tags=["audit"])


@router.get("/audit")
def audit_logs(
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
    outcome: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    if not x_persona_id:
        raise HTTPException(status_code=401, detail="Yêu cầu đăng nhập")
    try:
        persona = get_persona(x_persona_id)
    except UnknownPersona as exc:
        raise HTTPException(status_code=401, detail="Tài khoản không tồn tại hoặc đã bị vô hiệu hóa") from exc
    if persona.role not in {"hr_owner", "security", "manager"}:
        raise HTTPException(status_code=403, detail="Chỉ owner/manager được xem audit")
    return {
        "items": query_audit_logs(
            user_id=None,
            outcome=outcome,
            since=since,
            until=until,
            limit=limit,
            offset=offset,
        )
    }
