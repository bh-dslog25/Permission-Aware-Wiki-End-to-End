"""Administrative endpoints for database-backed accounts."""
from __future__ import annotations

import logging
import re
from typing import Literal
from uuid import uuid4

import httpx
from fastapi import APIRouter, Header, HTTPException
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.auth import UnknownPersona, get_persona, hash_password
from app.db import get_conn, write_audit

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
logger = logging.getLogger(__name__)


def _sync_authz_outbox() -> str:
    from app.authz.seed import process_outbox

    try:
        result = process_outbox()
    except (httpx.HTTPError, RuntimeError) as exc:
        logger.warning("Account change saved; authorization sync is pending: %s", exc)
        return "pending"
    return "completed" if result["failed"] == 0 else "pending"


class CreateUserRequest(BaseModel):
    user_code: str = Field(min_length=2, max_length=100)
    display_name: str = Field(min_length=1, max_length=150)
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=12, max_length=256)
    context_id: str = Field(min_length=1, max_length=150)
    unit_code: str = Field(min_length=1, max_length=50)
    role_code: str = Field(min_length=1, max_length=50)
    job_title: str = Field(default="", max_length=150)
    membership_type: Literal["primary", "matrix", "project"] = "primary"


class UpdateUserRequest(BaseModel):
    status: Literal["active", "disabled"]

class CreateMembershipRequest(BaseModel):
    context_id: str = Field(min_length=1, max_length=150)
    unit_code: str = Field(min_length=1, max_length=50)
    role_code: str = Field(min_length=1, max_length=50)
    job_title: str = Field(default="", max_length=150)
    membership_type: Literal["primary", "matrix", "project"] = "primary"


class UpdateMembershipRequest(BaseModel):
    role_code: str = Field(min_length=1, max_length=50)


def _admin_actor(x_persona_id: str | None) -> str:
    if not x_persona_id:
        raise HTTPException(status_code=401, detail="Yêu cầu đăng nhập")
    try:
        actor = get_persona(x_persona_id)
    except UnknownPersona as exc:
        raise HTTPException(status_code=401, detail="Tài khoản không tồn tại hoặc đã bị vô hiệu hóa") from exc
    with get_conn() as conn:
        is_security = conn.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM users u
                JOIN memberships m ON m.user_id = u.user_id
                JOIN role_assignments ra ON ra.membership_id = m.membership_id
                JOIN roles r ON r.role_id = ra.role_id
                WHERE u.user_code = %s AND u.status = 'active'
                  AND m.status = 'active' AND ra.status = 'active'
                  AND m.valid_from <= now()
                  AND (m.valid_to IS NULL OR m.valid_to > now())
                  AND ra.valid_from <= now()
                  AND (ra.valid_to IS NULL OR ra.valid_to > now())
                  AND r.role_code = 'security'
            )
            """,
            (actor.user_id,),
        ).fetchone()[0]
    if not is_security:
        raise HTTPException(status_code=403, detail="Chỉ security được quản lý tài khoản")
    return actor.user_id


def _has_other_security_admin(
    conn,
    *,
    excluded_user_code: str | None = None,
    excluded_membership_id: str | None = None,
) -> bool:
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext('permission-aware-wiki-security-admin'))"
    )
    return conn.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM users u
            JOIN memberships m ON m.user_id = u.user_id
            JOIN role_assignments ra ON ra.membership_id = m.membership_id
            JOIN roles r ON r.role_id = ra.role_id
            JOIN organization_units unit ON unit.unit_id = m.unit_id
            WHERE u.status = 'active' AND m.status = 'active'
              AND ra.status = 'active' AND r.role_code = 'security'
              AND unit.status = 'active'
              AND m.valid_from <= now() AND (m.valid_to IS NULL OR m.valid_to > now())
              AND ra.valid_from <= now() AND (ra.valid_to IS NULL OR ra.valid_to > now())
              AND (%s IS NULL OR u.user_code <> %s)
              AND (%s IS NULL OR m.membership_id <> %s)
        )
        """,
        (
            excluded_user_code, excluded_user_code,
            excluded_membership_id, excluded_membership_id,
        ),
    ).fetchone()[0]


@router.post("/users", status_code=201)
def create_user(
    payload: CreateUserRequest,
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
) -> dict:
    actor_id = _admin_actor(x_persona_id)
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", payload.user_code):
        raise HTTPException(status_code=422, detail="user_code chứa ký tự không hợp lệ")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", payload.email):
        raise HTTPException(status_code=422, detail="Email không hợp lệ")

    try:
        with get_conn() as conn, conn.transaction():
            unit = conn.execute(
                "SELECT unit_id FROM organization_units WHERE unit_code = %s AND status = 'active'",
                (payload.unit_code,),
            ).fetchone()
            role = conn.execute(
                "SELECT role_id FROM roles WHERE role_code = %s",
                (payload.role_code,),
            ).fetchone()
            if unit is None:
                raise HTTPException(status_code=422, detail="Đơn vị không tồn tại hoặc đã ngừng hoạt động")
            if role is None:
                raise HTTPException(status_code=422, detail="Vai trò không hợp lệ")
            user_id = conn.execute(
                """
                INSERT INTO users (user_code, display_name, email, password_hash)
                VALUES (%s, %s, %s, %s)
                RETURNING user_id
                """,
                (
                    payload.user_code,
                    payload.display_name,
                    payload.email,
                    hash_password(payload.password),
                ),
            ).fetchone()[0]
            membership_id = conn.execute(
                """
                INSERT INTO memberships (
                    user_id, unit_id, context_id, membership_type, job_title
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING membership_id
                """,
                (user_id, unit[0], payload.context_id, payload.membership_type, payload.job_title),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO role_assignments (membership_id, role_id) VALUES (%s, %s)",
                (membership_id, role[0]),
            )
            conn.execute(
                """
                INSERT INTO authz_outbox (idempotency_key, event_type, tuple_payload)
                VALUES (%s, 'user_membership_created', %s)
                """,
                (
                    f"membership-created:{membership_id}",
                    Jsonb({"user": f"user:{payload.user_code}", "relation": "assigned",
                           "object": f"unit:{payload.unit_code}"}),
                ),
            )
            write_audit(
                conn,
                actor_id,
                context_unit=payload.unit_code,
                structured_query={
                    "type": "user_created",
                    "user_code": payload.user_code,
                    "membership_id": str(membership_id),
                    "role_code": payload.role_code,
                },
                authz_decision={"action": "create_user", "decision": "success"},
                outcome="answered",
            )
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="Mã tài khoản, email hoặc context đã tồn tại") from exc

    sync_status = _sync_authz_outbox()
    return {
        "user_code": payload.user_code,
        "display_name": payload.display_name,
        "email": payload.email,
        "status": "active",
        "context_id": payload.context_id,
        "unit_code": payload.unit_code,
        "role_code": payload.role_code,
        "authorization_sync": sync_status,
    }


@router.get("/users")
def list_users(
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
) -> dict:
    _admin_actor(x_persona_id)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT u.user_code, u.display_name, u.email, u.status,
                   m.membership_id, m.context_id, m.membership_type, m.status,
                   m.valid_from, m.valid_to, m.job_title,
                   unit.unit_code, unit.name,
                   r.role_code, r.role_name, ra.status
            FROM users u
            LEFT JOIN memberships m ON m.user_id = u.user_id
            LEFT JOIN organization_units unit ON unit.unit_id = m.unit_id
            LEFT JOIN role_assignments ra ON ra.membership_id = m.membership_id
            LEFT JOIN roles r ON r.role_id = ra.role_id
            ORDER BY u.user_code, m.context_id, r.role_code
            """
        ).fetchall()
        units = conn.execute(
            """
            SELECT unit_code, name FROM organization_units
            WHERE status = 'active' ORDER BY name
            """
        ).fetchall()
        roles = conn.execute(
            "SELECT role_code, role_name FROM roles ORDER BY role_name"
        ).fetchall()

    users: dict[str, dict] = {}
    for row in rows:
        user_code, name, email, status = row[:4]
        user = users.setdefault(
            user_code,
            {"user_code": user_code, "display_name": name, "email": email,
             "status": status, "memberships": []},
        )
        membership_id = row[4]
        if membership_id is None:
            continue
        membership = next(
            (item for item in user["memberships"] if item["membership_id"] == str(membership_id)),
            None,
        )
        if membership is None:
            membership = {
                "membership_id": str(membership_id),
                "context_id": row[5],
                "membership_type": row[6],
                "status": row[7],
                "valid_from": row[8].isoformat() if row[8] else None,
                "valid_to": row[9].isoformat() if row[9] else None,
                "job_title": row[10],
                "unit_code": row[11],
                "unit_name": row[12],
                "roles": [],
            }
            user["memberships"].append(membership)
        if row[13] is not None:
            membership["roles"].append({
                "role_code": row[13],
                "role_name": row[14],
                "status": row[15],
            })

    return {
        "items": list(users.values()),
        "units": [{"unit_code": code, "name": name} for code, name in units],
        "roles": [{"role_code": code, "role_name": name} for code, name in roles],
    }


@router.post("/users/{user_code}/memberships", status_code=201)
def create_membership(
    user_code: str,
    payload: CreateMembershipRequest,
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
) -> dict:
    actor_id = _admin_actor(x_persona_id)
    try:
        with get_conn() as conn, conn.transaction():
            user = conn.execute(
                "SELECT user_id FROM users WHERE user_code = %s AND status = 'active' FOR UPDATE",
                (user_code,),
            ).fetchone()
            unit = conn.execute(
                "SELECT unit_id FROM organization_units WHERE unit_code = %s AND status = 'active'",
                (payload.unit_code,),
            ).fetchone()
            role = conn.execute(
                "SELECT role_id FROM roles WHERE role_code = %s",
                (payload.role_code,),
            ).fetchone()
            if user is None:
                raise HTTPException(status_code=404, detail="Tài khoản không tồn tại hoặc đã đóng")
            if unit is None:
                raise HTTPException(status_code=422, detail="Đơn vị không tồn tại hoặc đã ngừng hoạt động")
            if role is None:
                raise HTTPException(status_code=422, detail="Vai trò không hợp lệ")
            membership_id = conn.execute(
                """
                INSERT INTO memberships (
                    user_id, unit_id, context_id, membership_type, job_title
                )
                VALUES (%s, %s, %s, %s, %s)
                RETURNING membership_id
                """,
                (user[0], unit[0], payload.context_id, payload.membership_type, payload.job_title),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO role_assignments (membership_id, role_id) VALUES (%s, %s)",
                (membership_id, role[0]),
            )
            conn.execute(
                """
                INSERT INTO authz_outbox (idempotency_key, event_type, tuple_payload)
                VALUES (%s, 'user_membership_created', %s)
                """,
                (
                    f"membership-created:{membership_id}",
                    Jsonb({"user": f"user:{user_code}", "relation": "assigned",
                           "object": f"unit:{payload.unit_code}"}),
                ),
            )
            write_audit(
                conn,
                actor_id,
                context_unit=payload.unit_code,
                structured_query={
                    "type": "membership_created",
                    "user_code": user_code,
                    "membership_id": str(membership_id),
                    "role_code": payload.role_code,
                },
                authz_decision={"action": "create_membership", "decision": "success"},
                outcome="answered",
            )
    except UniqueViolation as exc:
        raise HTTPException(status_code=409, detail="Context hoặc role membership đã tồn tại") from exc

    return {"membership_id": str(membership_id), "authorization_sync": _sync_authz_outbox()}


@router.patch("/memberships/{membership_id}")
def update_membership_role(
    membership_id: str,
    payload: UpdateMembershipRequest,
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
) -> dict:
    actor_id = _admin_actor(x_persona_id)
    with get_conn() as conn, conn.transaction():
        role = conn.execute(
            "SELECT role_id FROM roles WHERE role_code = %s",
            (payload.role_code,),
        ).fetchone()
        if role is None:
            raise HTTPException(status_code=422, detail="Vai trò không hợp lệ")
        membership = conn.execute(
            """
            SELECT m.context_id, unit.unit_code, u.user_code,
                   EXISTS (
                       SELECT 1 FROM role_assignments current_ra
                       JOIN roles current_role ON current_role.role_id = current_ra.role_id
                       WHERE current_ra.membership_id = m.membership_id
                         AND current_ra.status = 'active'
                         AND current_role.role_code = 'security'
                   )
            FROM memberships m
            JOIN organization_units unit ON unit.unit_id = m.unit_id
            JOIN users u ON u.user_id = m.user_id
            WHERE m.membership_id = %s AND m.status = 'active'
            FOR UPDATE OF m
            """,
            (membership_id,),
        ).fetchone()
        if membership is None:
            raise HTTPException(status_code=404, detail="Membership không tồn tại hoặc đã thu hồi")
        if membership[3] and payload.role_code != "security" and not _has_other_security_admin(
            conn, excluded_membership_id=membership_id
        ):
            raise HTTPException(status_code=409, detail="Không thể gỡ role security cuối cùng")
        conn.execute(
            """
            UPDATE role_assignments
            SET status = 'revoked', valid_to = now()
            WHERE membership_id = %s AND status = 'active'
            """,
            (membership_id,),
        )
        conn.execute(
            """
            INSERT INTO role_assignments (membership_id, role_id)
            VALUES (%s, %s)
            ON CONFLICT (membership_id, role_id) DO UPDATE
            SET status = 'active', valid_from = now(), valid_to = NULL
            """,
            (membership_id, role[0]),
        )
        write_audit(
            conn,
            actor_id,
            context_unit=membership[1],
            structured_query={
                "type": "membership_role_changed",
                "user_code": membership[2],
                "context_id": membership[0],
                "role_code": payload.role_code,
            },
            authz_decision={"action": "update_membership_role", "decision": "success"},
            outcome="answered",
        )
    return {"membership_id": membership_id, "role_code": payload.role_code}


@router.delete("/memberships/{membership_id}")
def revoke_membership(
    membership_id: str,
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
) -> dict:
    actor_id = _admin_actor(x_persona_id)
    with get_conn() as conn, conn.transaction():
        membership = conn.execute(
            """
            SELECT m.context_id, unit.unit_code, u.user_code,
                   EXISTS (
                       SELECT 1 FROM role_assignments current_ra
                       JOIN roles current_role ON current_role.role_id = current_ra.role_id
                       WHERE current_ra.membership_id = m.membership_id
                         AND current_ra.status = 'active'
                         AND current_role.role_code = 'security'
                   )
            FROM memberships m
            JOIN organization_units unit ON unit.unit_id = m.unit_id
            JOIN users u ON u.user_id = m.user_id
            WHERE m.membership_id = %s AND m.status = 'active'
            FOR UPDATE OF m
            """,
            (membership_id,),
        ).fetchone()
        if membership is None:
            raise HTTPException(status_code=404, detail="Membership không tồn tại hoặc đã thu hồi")
        if membership[3] and not _has_other_security_admin(
            conn, excluded_membership_id=membership_id
        ):
            raise HTTPException(status_code=409, detail="Không thể thu hồi membership security cuối cùng")
        conn.execute(
            "UPDATE memberships SET status = 'inactive', valid_to = now() WHERE membership_id = %s",
            (membership_id,),
        )
        conn.execute(
            """
            UPDATE role_assignments SET status = 'revoked', valid_to = now()
            WHERE membership_id = %s AND status = 'active'
            """,
            (membership_id,),
        )
        conn.execute(
            """
            INSERT INTO authz_outbox (idempotency_key, event_type, tuple_payload)
            VALUES (%s, 'user_membership_revoked', %s)
            """,
            (
                f"membership-revoked:{membership_id}",
                Jsonb({"user": f"user:{membership[2]}", "relation": "assigned",
                       "object": f"unit:{membership[1]}"}),
            ),
        )
        write_audit(
            conn,
            actor_id,
            context_unit=membership[1],
            structured_query={
                "type": "membership_revoked",
                "user_code": membership[2],
                "context_id": membership[0],
            },
            authz_decision={"action": "revoke_membership", "decision": "success"},
            outcome="answered",
        )
    return {"membership_id": membership_id, "authorization_sync": _sync_authz_outbox()}


@router.patch("/users/{user_code}")
def update_user_status(
    user_code: str,
    payload: UpdateUserRequest,
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
) -> dict:
    actor_id = _admin_actor(x_persona_id)
    with get_conn() as conn, conn.transaction():
        row = conn.execute(
            """
            SELECT user_id, status
            FROM users
            WHERE user_code = %s
            FOR UPDATE
            """,
            (user_code,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Không tìm thấy tài khoản")
        if row[1] == payload.status:
            return {"user_code": user_code, "status": row[1], "authorization_sync": "not_needed"}
        if payload.status == "disabled":
            if user_code == actor_id:
                raise HTTPException(status_code=409, detail="Không thể tự khóa tài khoản đang quản trị")
            has_security_role = conn.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM memberships m
                    JOIN role_assignments ra ON ra.membership_id = m.membership_id
                    JOIN roles r ON r.role_id = ra.role_id
                    WHERE m.user_id = %s AND m.status = 'active'
                      AND ra.status = 'active' AND r.role_code = 'security'
                      AND m.valid_from <= now()
                      AND (m.valid_to IS NULL OR m.valid_to > now())
                      AND ra.valid_from <= now()
                      AND (ra.valid_to IS NULL OR ra.valid_to > now())
                )
                """,
                (row[0],),
            ).fetchone()[0]
            if has_security_role and not _has_other_security_admin(
                conn, excluded_user_code=user_code
            ):
                raise HTTPException(status_code=409, detail="Không thể đóng tài khoản security cuối cùng")
        conn.execute(
            "UPDATE users SET status = %s, updated_at = now() WHERE user_id = %s",
            (payload.status, row[0]),
        )
        memberships = conn.execute(
            """
            SELECT unit.unit_code
            FROM memberships m
            JOIN organization_units unit ON unit.unit_id = m.unit_id
            WHERE m.user_id = %s
              AND m.status = 'active'
              AND unit.status = 'active'
              AND m.valid_from <= now()
              AND (m.valid_to IS NULL OR m.valid_to > now())
            """,
            (row[0],),
        ).fetchall()
        conn.execute(
            """
            INSERT INTO authz_outbox (idempotency_key, event_type, tuple_payload)
            VALUES (%s, 'user_status_changed', %s)
            """,
            (
                f"user-status:{uuid4()}",
                Jsonb({"user": f"user:{user_code}", "status": payload.status,
                       "units": [unit[0] for unit in memberships]}),
            ),
        )
        write_audit(
            conn,
            actor_id,
            structured_query={"type": "user_status_changed", "user_code": user_code, "status": payload.status},
            authz_decision={"action": "update_user_status", "decision": "success"},
            outcome="answered",
        )
    sync_status = _sync_authz_outbox()
    return {"user_code": user_code, "status": payload.status, "authorization_sync": sync_status}
