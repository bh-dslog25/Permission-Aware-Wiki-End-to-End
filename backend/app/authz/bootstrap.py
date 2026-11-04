"""Create the first security administrator and root organization unit."""
from __future__ import annotations

from getpass import getpass

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from app.auth import hash_password
from app.db import get_conn, init_schema, write_audit

ADMIN_USER_CODE = "admin"
ADMIN_DISPLAY_NAME = "Admin"
ADMIN_EMAIL = "admin@permission-aware-wiki.local"
ROOT_UNIT_CODE = "root"
ROOT_UNIT_NAME = "Organization"


def bootstrap_admin(
    *,
    password: str,
) -> None:
    if len(password) < 12:
        raise ValueError("Mật khẩu phải có ít nhất 12 ký tự")

    init_schema()
    try:
        with get_conn() as conn, conn.transaction():
            conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext('permission-aware-wiki-bootstrap'))"
            )
            has_security_admin = conn.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM role_assignments ra
                    JOIN roles r ON r.role_id = ra.role_id
                    JOIN memberships m ON m.membership_id = ra.membership_id
                    JOIN users u ON u.user_id = m.user_id
                    WHERE r.role_code = 'security'
                      AND u.status = 'active'
                      AND m.status = 'active'
                      AND ra.status = 'active'
                )
                """
            ).fetchone()[0]
            if has_security_admin:
                raise RuntimeError("Đã có tài khoản security active; từ chối bootstrap lần nữa")

            existing_admin = conn.execute(
                """
                SELECT EXISTS (SELECT 1 FROM users WHERE user_code = %s)
                """,
                (ADMIN_USER_CODE,),
            ).fetchone()[0]
            if existing_admin:
                raise RuntimeError("Tài khoản admin đã tồn tại; không ghi đè mật khẩu.")

            unit = conn.execute(
                """
                SELECT unit_id, unit_type, parent_unit_id, status
                FROM organization_units
                WHERE unit_code = %s
                """,
                (ROOT_UNIT_CODE,),
            ).fetchone()
            if unit is None:
                unit_id = conn.execute(
                    """
                    INSERT INTO organization_units (unit_code, name, unit_type)
                    VALUES (%s, %s, 'group')
                    RETURNING unit_id
                    """,
                    (ROOT_UNIT_CODE, ROOT_UNIT_NAME),
                ).fetchone()[0]
            else:
                unit_id = unit[0]
                if unit[1] != "group" or unit[2] is not None or unit[3] != "active":
                    raise ValueError("Đơn vị bootstrap phải là group gốc đang active")

            role_id = conn.execute(
                "SELECT role_id FROM roles WHERE role_code = 'security'"
            ).fetchone()
            if role_id is None:
                raise RuntimeError("Không tìm thấy role security; hãy kiểm tra schema.sql")

            user_id = conn.execute(
                """
                INSERT INTO users (user_code, display_name, email, password_hash)
                VALUES (%s, %s, %s, %s)
                RETURNING user_id
                """,
                (
                    ADMIN_USER_CODE,
                    ADMIN_DISPLAY_NAME,
                    ADMIN_EMAIL,
                    hash_password(password),
                ),
            ).fetchone()[0]
            membership_id = conn.execute(
                """
                INSERT INTO memberships (
                    user_id, unit_id, context_id, membership_type, job_title
                )
                VALUES (%s, %s, %s, 'primary', 'Security Administrator')
                RETURNING membership_id
                """,
                (user_id, unit_id, f"bootstrap:{ADMIN_USER_CODE}"),
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO role_assignments (membership_id, role_id) VALUES (%s, %s)",
                (membership_id, role_id[0]),
            )
            conn.execute(
                """
                INSERT INTO authz_outbox (idempotency_key, event_type, tuple_payload)
                VALUES (%s, 'user_membership_created', %s)
                """,
                (
                    f"bootstrap-membership:{membership_id}",
                    Jsonb({
                        "user": f"user:{ADMIN_USER_CODE}",
                        "relation": "assigned",
                        "object": f"unit:{ROOT_UNIT_CODE}",
                    }),
                ),
            )
            write_audit(
                conn,
                ADMIN_USER_CODE,
                context_unit=ROOT_UNIT_CODE,
                structured_query={"type": "security_admin_bootstrap"},
                authz_decision={"action": "bootstrap_security_admin", "decision": "success"},
                outcome="answered",
            )
    except UniqueViolation as exc:
        raise RuntimeError("Mã tài khoản hoặc email đã tồn tại") from exc


def main() -> None:
    password = getpass("Mật khẩu (ít nhất 12 ký tự): ")
    confirmation = getpass("Nhập lại mật khẩu: ")
    if password != confirmation:
        raise ValueError("Hai mật khẩu không khớp")

    bootstrap_admin(password=password)
    print(
        f"Đã tạo tài khoản {ADMIN_USER_CODE} ({ADMIN_EMAIL}). "
        "Hãy chạy seed OpenFGA trước khi sử dụng API."
    )


if __name__ == "__main__":
    main()
