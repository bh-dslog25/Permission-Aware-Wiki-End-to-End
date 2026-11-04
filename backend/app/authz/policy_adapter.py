"""Policy Adapter: OpenFGA (ReBAC) + ABAC  ->  Constraints  ->  bộ lọc SQL.

- Quan hệ tổ chức do OpenFGA quyết định (check + list-objects). Adapter KHÔNG tự duyệt cây tổ chức.
- ABAC (độ mật, thời điểm) được ghép ở đây.
- Lỗi OpenFGA (mạng, 5xx...) => ném exception => không có Constraints => không truy xuất được gì (fail closed).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any, NamedTuple

import httpx

from app.db import CLASSIFICATION_LEVELS, get_conn
from app.auth import InvalidContext, context_unit as lookup_context_unit, unit_name

DEFAULT_STORE_NAME = "permission-aware-rag-wiki"


class AuthzError(Exception):
    pass


class ContextNotAssigned(AuthzError):
    """Người dùng không được gán ngữ cảnh (đơn vị) này theo OpenFGA."""


class UnknownClassification(AuthzError):
    pass


class SqlFilter(NamedTuple):
    clause: str
    params: dict


@dataclass(frozen=True)
class Constraints:
    """Ràng buộc bắt buộc cho retrieval. Không có giá trị mặc định ở nơi dùng."""

    user_id: str
    context_unit: str
    allowed_units: tuple[str, ...]  # từ OpenFGA (ReBAC)
    max_classification: int         # từ ABAC (clearance), theo CLASSIFICATION_LEVELS
    as_of: date                     # thời điểm T để lọc hiệu lực
    unit_chain: tuple[str, ...] = ()
    context_label: str = ""

    @property
    def allowed_classifications(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, rank in sorted(CLASSIFICATION_LEVELS.items(), key=lambda item: item[1])
            if rank <= self.max_classification
        )

    def to_sql(self) -> SqlFilter:
        """Điều kiện WHERE cho bảng chunks (placeholder kiểu psycopg: %(name)s).

        - owner_unit: tập đơn vị do OpenFGA trả về. Danh sách rỗng => ANY('{}') là FALSE.
        - classification: ABAC. approved: chưa được Owner duyệt thì không truy xuất.
        - Hiệu lực: valid_from <= T <= valid_to (valid_to NULL = chưa hết hạn; NULL ở valid_from
          không bao giờ khớp). Hỏi quá khứ thì T lùi lại nên bản superseded/expired của thời điểm
          đó vẫn được lấy. Bản nháp luôn bị loại.
        """
        clause = (
            "owner_unit = ANY(%(auth_units)s) "
            "AND classification <= %(auth_max_class)s "
            "AND approved "
            "AND status <> 'draft' "
            "AND valid_from <= %(auth_as_of)s "
            "AND (valid_to IS NULL OR valid_to >= %(auth_as_of)s)"
        )
        params = {
            "auth_units": list(self.allowed_units),
            "auth_max_class": self.max_classification,
            "auth_as_of": self.as_of,
        }
        return SqlFilter(clause, params)


def _unit_chain(unit_id: str) -> tuple[str, ...]:
    chain: list[str] = []
    seen: set[str] = set()
    with get_conn() as conn:
        current = unit_id
        while current:
            if current in seen:
                raise AuthzError(f"Vòng lặp trong cây đơn vị tại {current!r}")
            seen.add(current)
            row = conn.execute(
                """
                SELECT child.unit_code, parent.unit_code
                FROM organization_units child
                LEFT JOIN organization_units parent ON parent.unit_id = child.parent_unit_id
                WHERE child.unit_code = %s AND child.status = 'active'
                """,
                (current,),
            ).fetchone()
            if row is None:
                raise ContextNotAssigned(f"Đơn vị {current!r} không tồn tại hoặc không hoạt động")
            chain.append(row[0])
            current = row[1]
    return tuple(reversed(chain))


def find_store_id(http: httpx.Client, name: str) -> str | None:
    token = ""
    while True:
        r = http.get("/stores", params={"page_size": 100, "continuation_token": token})
        r.raise_for_status()
        body = r.json()
        for s in body.get("stores", []):
            if s["name"] == name:
                return s["id"]
        token = body.get("continuation_token", "")
        if not token:
            return None


class PolicyAdapter:
    def __init__(self, api_url: str | None = None, store_name: str | None = None,
                 client: httpx.Client | None = None):
        api_url = (api_url or os.getenv("OPENFGA_API_URL", "http://localhost:8080")).rstrip("/")
        self._store_name = store_name or os.getenv("OPENFGA_STORE_NAME", DEFAULT_STORE_NAME)
        self._http = client or httpx.Client(base_url=api_url, timeout=5.0)
        self._store_id: str | None = None

    def _path(self, suffix: str) -> str:
        if self._store_id is None:
            self._store_id = find_store_id(self._http, self._store_name)
            if self._store_id is None:
                raise AuthzError(f"Chưa có store '{self._store_name}'. Chạy seed trước.")
        return f"/stores/{self._store_id}{suffix}"

    def _post(self, suffix: str, body: dict) -> dict:
        r = self._http.post(self._path(suffix), json=body)
        r.raise_for_status()
        return r.json()

    def build_constraints(self, *, user_id: str, context_unit: str, clearance: str,
                          as_of: date | None = None) -> Constraints:
        try:
            unit_id = lookup_context_unit(context_unit)
        except InvalidContext as exc:
            raise ContextNotAssigned(str(exc)) from exc

        with get_conn() as conn:
            assignment = conn.execute(
                """
                SELECT r.max_classification
                FROM users u
                JOIN memberships m ON m.user_id = u.user_id
                JOIN organization_units unit ON unit.unit_id = m.unit_id
                JOIN role_assignments ra ON ra.membership_id = m.membership_id
                JOIN roles r ON r.role_id = ra.role_id
                WHERE u.user_code = %s
                  AND u.status = 'active'
                  AND m.context_id = %s
                  AND m.status = 'active'
                  AND m.valid_from <= now()
                  AND (m.valid_to IS NULL OR m.valid_to > now())
                  AND unit.status = 'active'
                  AND ra.status = 'active'
                  AND ra.valid_from <= now()
                  AND (ra.valid_to IS NULL OR ra.valid_to > now())
                ORDER BY r.max_classification DESC
                LIMIT 1
                """,
                (user_id, context_unit),
            ).fetchone()
        if assignment is None:
            raise ContextNotAssigned(f"{user_id} không có membership/role active cho {context_unit}")
        max_classification = int(assignment[0])

        user = f"user:{user_id}"
        unit = f"unit:{unit_id}"

        # 1) Người này có thật sự được chọn ngữ cảnh này không? (OpenFGA quyết định)
        allowed = self._post("/check", {
            "tuple_key": {"user": user, "relation": "assigned", "object": unit},
        })["allowed"]
        if not allowed:
            raise ContextNotAssigned(f"{user_id} không được gán {context_unit}")

        # 2) ReBAC: các đơn vị người này xem được khi làm việc trong ĐÚNG ngữ cảnh đó.
        #    Ngữ cảnh được truyền dưới dạng contextual tuple (không ghi cố định).
        res = self._post("/list-objects", {
            "type": "unit",
            "relation": "viewer",
            "user": user,
            "contextual_tuples": {"tuple_keys": [
                {"user": user, "relation": "member", "object": unit},
            ]},
        })
        units = tuple(sorted(o.removeprefix("unit:") for o in res["objects"]))

        # 3) ABAC: độ mật tối đa theo clearance
        chain = _unit_chain(unit_id)
        return Constraints(
            user_id,
            context_unit,
            units,
            max_classification,
            as_of or date.today(),
            chain,
            unit_name(unit_id),
        )

    def build_scope(
        self,
        ctx: Any,
        *,
        clearance: str | None = None,
        as_of: "date | None" = None,
    ) -> Any:
        """Giao dien chinh cho pipeline.py (thoa man PolicyAdapter Protocol).

        ctx phai co user_id va context_id (RequestContext).
        Lay clearance tu ctx.clearance neu co, nguoc lai dung 'internal'.
        Chuyen Constraints -> UserScope (int max_classification).
        """
        try:
            from app.rag.retrieval import UserScope  # noqa: PLC0415
        except ImportError as e:
            raise ImportError("app.rag.retrieval chua duoc cai dat") from e

        effective_clearance = clearance or getattr(ctx, "clearance", "internal")
        c = self.build_constraints(
            user_id=ctx.user_id,
            context_unit=ctx.context_id,
            clearance=effective_clearance,
            as_of=as_of,
        )
        return UserScope(
            user_id=c.user_id,
            context_id=c.context_unit,
            allowed_unit_ids=c.allowed_units,
            max_classification=c.max_classification,
            unit_chain=c.unit_chain,
            unit_labels={u: unit_name(u) for u in set(c.allowed_units) | set(c.unit_chain)},
            context_label=c.context_label,
        )
