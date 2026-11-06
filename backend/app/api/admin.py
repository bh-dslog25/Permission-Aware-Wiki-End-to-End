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


OrganizationUnitType = Literal[
    "group", "company", "division", "department", "team", "project", "committee"
]


class CreateOrganizationUnitRequest(BaseModel):
    unit_code: str = Field(min_length=1, max_length=50, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=150)
    unit_type: OrganizationUnitType
    parent_unit_code: str | None = Field(default=None, max_length=50)


class UpdateOrganizationUnitRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    parent_unit_code: str | None = Field(default=None, max_length=50)
    status: Literal["active", "inactive"] | None = None


_ALLOWED_UNIT_PARENTS: dict[str, set[str]] = {
    "group": set(),
    "company": {"group"},
    "division": {"company"},
    "department": {"company", "division"},
    "team": {"department"},
    "project": {"group", "company", "division", "department", "team"},
    "committee": {"group", "company", "division", "department", "team"},
}


def _validate_unit_parent(unit_type: str, parent_type: str | None) -> None:
    allowed_parents = _ALLOWED_UNIT_PARENTS[unit_type]
    if parent_type is None:
        if unit_type != "group":
            raise HTTPException(status_code=422, detail="Chỉ đơn vị group mới có thể không có parent")
        return
    if parent_type not in allowed_parents:
        raise HTTPException(
            status_code=422,
            detail=f"Parent loại '{parent_type}' không hợp lệ cho đơn vị '{unit_type}'",
        )


def _enqueue_unit_relation_sync(conn, relations: dict[str, list[dict[str, str]]]) -> None:
    if not relations["writes"] and not relations["deletes"]:
        return
    conn.execute(
        """
        INSERT INTO authz_outbox (idempotency_key, event_type, tuple_payload)
        VALUES (%s, 'organization_unit_relations_changed', %s)
        """,
        (f"organization-unit:{uuid4()}", Jsonb(relations)),
    )


def _lock_organization_tree(conn) -> None:
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext('permission-aware-wiki-organization-tree'))"
    )


def _unit_relation_payload(
    *,
    unit_code: str,
    old_parent_code: str | None,
    new_parent_code: str | None,
    old_status: str,
    new_status: str,
) -> dict[str, list[dict[str, str]]]:
    def tuple_for(parent_code: str) -> dict[str, str]:
        return {
            "user": f"unit:{unit_code}",
            "relation": "child",
            "object": f"unit:{parent_code}",
        }

    deletes = (
        [tuple_for(old_parent_code)]
        if old_parent_code and old_status == "active"
        else []
    )
    writes = (
        [tuple_for(new_parent_code)]
        if new_parent_code and new_status == "active"
        else []
    )
    if deletes == writes:
        return {"writes": [], "deletes": []}
    return {"writes": writes, "deletes": deletes}


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
        raise HTTPException(status_code=403, detail="Chỉ security được phép quản trị")
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
                """
                SELECT unit_id FROM organization_units
                WHERE unit_code = %s AND status = 'active'
                FOR SHARE
                """,
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


@router.get("/organization-units")
def list_organization_units(
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
) -> dict:
    _admin_actor(x_persona_id)
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT child.unit_code, child.name, child.unit_type, child.status,
                   parent.unit_code AS parent_unit_code, parent.name AS parent_name
            FROM organization_units child
            LEFT JOIN organization_units parent ON parent.unit_id = child.parent_unit_id
            ORDER BY child.unit_type, child.name, child.unit_code
            """
        ).fetchall()
    return {
        "items": [
            {
                "unit_code": row[0],
                "name": row[1],
                "unit_type": row[2],
                "status": row[3],
                "parent_unit_code": row[4],
                "parent_name": row[5],
            }
            for row in rows
        ]
    }


@router.post("/organization-units", status_code=201)
def create_organization_unit(
    payload: CreateOrganizationUnitRequest,
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
) -> dict:
    actor_id = _admin_actor(x_persona_id)
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Tên organization unit không được để trống")
    parent_id = None
    parent_type = None
    with get_conn() as conn, conn.transaction():
        _lock_organization_tree(conn)
        if payload.parent_unit_code is not None:
            parent = conn.execute(
                """
                SELECT unit_id, unit_type
                FROM organization_units
                WHERE unit_code = %s AND status = 'active'
                FOR SHARE
                """,
                (payload.parent_unit_code,),
            ).fetchone()
            if parent is None:
                raise HTTPException(status_code=422, detail="Đơn vị cha không tồn tại hoặc đã ngừng hoạt động")
            parent_id, parent_type = parent
        _validate_unit_parent(payload.unit_type, parent_type)
        try:
            conn.execute(
                """
                INSERT INTO organization_units (unit_code, name, unit_type, parent_unit_id)
                VALUES (%s, %s, %s, %s)
                """,
                (payload.unit_code, payload.name.strip(), payload.unit_type, parent_id),
            )
        except UniqueViolation as exc:
            raise HTTPException(status_code=409, detail="Mã organization unit đã tồn tại") from exc

        relations = _unit_relation_payload(
            unit_code=payload.unit_code,
            old_parent_code=None,
            new_parent_code=payload.parent_unit_code,
            old_status="inactive",
            new_status="active",
        )
        _enqueue_unit_relation_sync(conn, relations)
        write_audit(
            conn,
            actor_id,
            context_unit=payload.unit_code,
            structured_query={
                "type": "organization_unit_created",
                "unit_code": payload.unit_code,
                "unit_type": payload.unit_type,
                "parent_unit_code": payload.parent_unit_code,
            },
            authz_decision={"action": "create_organization_unit", "decision": "success"},
            outcome="answered",
        )

    sync_status = _sync_authz_outbox() if relations["writes"] else "not_needed"
    return {
        "unit_code": payload.unit_code,
        "name": payload.name.strip(),
        "unit_type": payload.unit_type,
        "parent_unit_code": payload.parent_unit_code,
        "status": "active",
        "authorization_sync": sync_status,
    }


@router.patch("/organization-units/{unit_code}")
def update_organization_unit(
    unit_code: str,
    payload: UpdateOrganizationUnitRequest,
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
) -> dict:
    actor_id = _admin_actor(x_persona_id)
    fields = payload.model_fields_set
    with get_conn() as conn, conn.transaction():
        _lock_organization_tree(conn)
        unit = conn.execute(
            """
            SELECT child.unit_id, child.name, child.unit_type, child.status,
                   child.parent_unit_id, parent.unit_code, parent.unit_type, parent.status
            FROM organization_units child
            LEFT JOIN organization_units parent ON parent.unit_id = child.parent_unit_id
            WHERE child.unit_code = %s
            FOR UPDATE OF child
            """,
            (unit_code,),
        ).fetchone()
        if unit is None:
            raise HTTPException(status_code=404, detail="Organization unit không tồn tại")

        unit_id, old_name, unit_type, old_status, old_parent_id, old_parent_code, old_parent_type, old_parent_status = unit
        if not fields:
            return {
                "unit_code": unit_code,
                "name": old_name,
                "unit_type": unit_type,
                "parent_unit_code": old_parent_code,
                "status": old_status,
                "authorization_sync": "not_needed",
            }
        if "name" in fields and payload.name is None:
            raise HTTPException(status_code=422, detail="Tên organization unit không được để trống")
        if "status" in fields and payload.status is None:
            raise HTTPException(status_code=422, detail="Trạng thái organization unit không hợp lệ")

        new_name = payload.name.strip() if "name" in fields and payload.name is not None else old_name
        if "name" in fields and not new_name:
            raise HTTPException(status_code=422, detail="Tên organization unit không được để trống")
        new_status = payload.status if "status" in fields and payload.status is not None else old_status
        parent_changed = (
            "parent_unit_code" in fields and payload.parent_unit_code != old_parent_code
        )
        new_parent_code = payload.parent_unit_code if parent_changed else old_parent_code
        new_parent_id = old_parent_id
        new_parent_type = old_parent_type
        new_parent_status = old_parent_status

        should_validate_parent = parent_changed or (new_status == "active" and old_status != "active")
        if should_validate_parent:
            new_parent_id = None
            new_parent_type = None
            new_parent_status = None
            if new_parent_code is not None:
                parent = conn.execute(
                    """
                    SELECT unit_id, unit_type, status
                    FROM organization_units
                    WHERE unit_code = %s
                    FOR SHARE
                    """,
                    (new_parent_code,),
                ).fetchone()
                if parent is None or parent[2] != "active":
                    raise HTTPException(status_code=422, detail="Đơn vị cha không tồn tại hoặc đã ngừng hoạt động")
                new_parent_id, new_parent_type, new_parent_status = parent
            _validate_unit_parent(unit_type, new_parent_type)
            if new_parent_id is not None:
                is_descendant = conn.execute(
                    """
                    WITH RECURSIVE descendants(unit_id) AS (
                        SELECT %s::uuid
                        UNION
                        SELECT child.unit_id
                        FROM organization_units child
                        JOIN descendants parent ON child.parent_unit_id = parent.unit_id
                    )
                    SELECT EXISTS (
                        SELECT 1 FROM descendants WHERE unit_id = %s
                    )
                    """,
                    (unit_id, new_parent_id),
                ).fetchone()[0]
                if is_descendant:
                    raise HTTPException(status_code=422, detail="Không thể chuyển đơn vị xuống dưới đơn vị con của nó")

        if new_status == "inactive" and old_status != "inactive":
            has_active_children = conn.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM organization_units
                    WHERE parent_unit_id = %s AND status = 'active'
                )
                """,
                (unit_id,),
            ).fetchone()[0]
            has_active_memberships = conn.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM memberships
                    WHERE unit_id = %s AND status = 'active'
                      AND valid_from <= now() AND (valid_to IS NULL OR valid_to > now())
                )
                """,
                (unit_id,),
            ).fetchone()[0]
            if has_active_children or has_active_memberships:
                raise HTTPException(
                    status_code=409,
                    detail="Không thể ngừng hoạt động khi đơn vị còn đơn vị con hoặc membership đang hoạt động",
                )

        conn.execute(
            """
            UPDATE organization_units
            SET name = %s, parent_unit_id = %s, status = %s
            WHERE unit_id = %s
            """,
            (new_name, new_parent_id, new_status, unit_id),
        )
        relations = _unit_relation_payload(
            unit_code=unit_code,
            old_parent_code=old_parent_code,
            new_parent_code=new_parent_code,
            old_status=old_status,
            new_status=new_status,
        )
        _enqueue_unit_relation_sync(conn, relations)
        write_audit(
            conn,
            actor_id,
            context_unit=unit_code,
            structured_query={
                "type": "organization_unit_updated",
                "unit_code": unit_code,
                "name": new_name,
                "parent_unit_code": new_parent_code,
                "status": new_status,
            },
            authz_decision={"action": "update_organization_unit", "decision": "success"},
            outcome="answered",
        )

    sync_status = _sync_authz_outbox() if relations["writes"] or relations["deletes"] else "not_needed"
    return {
        "unit_code": unit_code,
        "name": new_name,
        "unit_type": unit_type,
        "parent_unit_code": new_parent_code,
        "status": new_status,
        "authorization_sync": sync_status,
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
                """
                SELECT unit_id FROM organization_units
                WHERE unit_code = %s AND status = 'active'
                FOR SHARE
                """,
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
