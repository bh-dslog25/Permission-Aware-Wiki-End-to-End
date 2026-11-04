"""Load active user identities and memberships from PostgreSQL."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from app.db import CLASSIFICATION_NAMES, get_conn, write_audit

PERSONA_ALIASES = {
    "an": "u_minh_anh",
    "binh": "u_quoc_bao",
    "chi": "u_thu_ha",
    "lan": "u_pham_lan",
}
PASSWORD_ITERATIONS = 310_000


class UnknownPersona(Exception):
    pass


class InvalidContext(Exception):
    pass


@dataclass(frozen=True)
class Persona:
    user_id: str
    name: str
    clearance: str
    allowed_contexts: tuple[str, ...]
    role: str = "employee"
    description: str = ""
    team_id: str | None = None
    department_id: str | None = None
    company_id: str | None = None
    email: str = ""
    title: str = ""
    is_persona: bool = True
    memberships: tuple[dict, ...] = ()

    @property
    def id(self) -> str:
        return self.user_id

    @property
    def contexts(self) -> tuple[str, ...]:
        return self.allowed_contexts


def _persona_rows(user_code: str | None = None) -> list[Persona]:
    clauses = ["u.status = 'active'"]
    params: list[str] = []
    if user_code is not None:
        clauses.append("(u.user_code = %s OR u.email = %s)")
        params.extend((user_code, user_code))

    with get_conn() as conn:
        rows = conn.execute(
            f"""
            SELECT u.user_code, u.display_name, u.email, u.description,
                   m.context_id, unit.unit_code, unit.name, unit.unit_type,
                   membership_role.role_code, membership_role.max_classification,
                   m.membership_type, m.job_title
            FROM users u
            LEFT JOIN memberships m
              ON m.user_id = u.user_id
             AND m.status = 'active'
             AND m.valid_from <= now()
             AND (m.valid_to IS NULL OR m.valid_to > now())
             AND EXISTS (
                 SELECT 1 FROM role_assignments active_ra
                 WHERE active_ra.membership_id = m.membership_id
                   AND active_ra.status = 'active'
                   AND active_ra.valid_from <= now()
                   AND (active_ra.valid_to IS NULL OR active_ra.valid_to > now())
             )
            LEFT JOIN organization_units unit
              ON unit.unit_id = m.unit_id AND unit.status = 'active'
            LEFT JOIN LATERAL (
                SELECT r.role_code, r.max_classification
                FROM role_assignments ra
                JOIN roles r ON r.role_id = ra.role_id
                WHERE ra.membership_id = m.membership_id
                  AND ra.status = 'active'
                  AND ra.valid_from <= now()
                  AND (ra.valid_to IS NULL OR ra.valid_to > now())
                ORDER BY r.max_classification DESC, r.role_code
                LIMIT 1
            ) membership_role ON TRUE
            WHERE {" AND ".join(clauses)}
            ORDER BY u.user_code,
                     CASE m.membership_type WHEN 'primary' THEN 0 ELSE 1 END,
                     m.context_id
            """,
            params,
        ).fetchall()

    by_user: dict[str, dict] = {}
    for row in rows:
        (
            user_code_value, name, email, description, context_id, unit_code,
            unit_label, unit_type, role_code, max_classification, membership_type, job_title,
        ) = row
        record = by_user.setdefault(
            user_code_value,
            {
                "name": name,
                "email": email,
                "description": description,
                "memberships": [],
            },
        )
        if not context_id or not unit_code:
            continue
        record["memberships"].append(
            {
                "context_id": context_id,
                "team": unit_code,
                "unit_name": unit_label,
                "unit_type": unit_type,
                "role": role_code or "employee",
                "clearance": CLASSIFICATION_NAMES.get(max_classification or 0, "public"),
                "membership_type": membership_type,
                "title": job_title,
            }
        )

    personas: list[Persona] = []
    for user_code_value, user in by_user.items():
        memberships = user["memberships"]
        primary = memberships[0] if memberships else {}
        personas.append(
            Persona(
                user_id=user_code_value,
                name=user["name"],
                email=user["email"],
                description=user["description"],
                allowed_contexts=tuple(m["context_id"] for m in memberships),
                memberships=tuple(memberships),
                role=primary.get("role", "employee"),
                clearance=primary.get("clearance", "public"),
                team_id=primary.get("team"),
                title=primary.get("title", ""),
                is_persona=True,
            )
        )
    return personas


def list_personas() -> list[Persona]:
    return _persona_rows()


def authenticate_user(identifier: str, password: str) -> Persona | None:
    """Authenticate an active database user using a PBKDF2 password hash."""
    identifier = PERSONA_ALIASES.get(identifier.strip().lower(), identifier.strip())
    if not identifier or not password:
        return None
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT user_code, password_hash
            FROM users
            WHERE status = 'active' AND (LOWER(email) = LOWER(%s) OR user_code = %s)
            """,
            (identifier, identifier),
        ).fetchone()
    if row is None or not row[1]:
        return None
    if not verify_password(password, row[1]):
        return None
    return get_persona(row[0])


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, salt, expected = encoded_hash.split("$", 2)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt), PASSWORD_ITERATIONS
        ).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def change_password(identifier: str, current_password: str, new_password: str) -> bool:
    identifier = PERSONA_ALIASES.get(identifier.strip().lower(), identifier.strip())
    with get_conn() as conn, conn.transaction():
        row = conn.execute(
            """
            SELECT user_id, user_code, password_hash
            FROM users
            WHERE status = 'active'
              AND (LOWER(email) = LOWER(%s) OR user_code = %s)
            FOR UPDATE
            """,
            (identifier, identifier),
        ).fetchone()
        if row is None or not row[2] or not verify_password(current_password, row[2]):
            return False
        conn.execute(
            """
            UPDATE users
            SET password_hash = %s, updated_at = now()
            WHERE user_id = %s
            """,
            (hash_password(new_password), row[0]),
        )
        write_audit(
            conn,
            row[1],
            structured_query={"type": "password_changed"},
            authz_decision={"action": "change_password", "decision": "success"},
            outcome="answered",
        )
        return True


def get_persona(persona_id: str) -> Persona:
    if not persona_id:
        raise UnknownPersona(persona_id)
    persona_id = PERSONA_ALIASES.get(persona_id.lower(), persona_id)
    personas = _persona_rows(persona_id)
    if not personas:
        raise UnknownPersona(persona_id)
    return personas[0]


class NeedsContextClarification(Exception):
    def __init__(self, available_contexts: tuple[str, ...]):
        super().__init__(f"User has multiple contexts: {available_contexts}")
        self.available_contexts = available_contexts


def resolve_context(persona: Persona, requested: str | None = None) -> str:
    if requested is None:
        if len(persona.allowed_contexts) > 1:
            raise NeedsContextClarification(persona.allowed_contexts)
        if not persona.allowed_contexts:
            raise InvalidContext(f"{persona.user_id} không có membership hợp lệ")
        return persona.allowed_contexts[0]
    if requested not in persona.allowed_contexts:
        raise InvalidContext(f"{persona.user_id} không được chọn ngữ cảnh {requested}")
    return requested


def context_unit(context_id: str) -> str:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT unit.unit_code
            FROM memberships m
            JOIN organization_units unit ON unit.unit_id = m.unit_id
            WHERE m.context_id = %s
              AND m.status = 'active'
              AND unit.status = 'active'
              AND m.valid_from <= now()
              AND (m.valid_to IS NULL OR m.valid_to > now())
            """,
            (context_id,),
        ).fetchone()
    if row is None:
        raise InvalidContext(f"Không tìm thấy membership hợp lệ cho {context_id!r}")
    return row[0]


def unit_name(unit_id: str) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM organization_units WHERE unit_code = %s AND status = 'active'",
            (unit_id,),
        ).fetchone()
    return row[0] if row else unit_id
