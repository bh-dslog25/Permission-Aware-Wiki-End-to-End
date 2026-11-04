"""Kết nối PostgreSQL, schema MVP và health check.

Nguồn chuẩn duy nhất (Single Source of Truth) cho schema và khởi tạo database.
Ref: README Section 3.1 & MVP Architecture.

Các bảng chính:
  1. users, organization_units, memberships, roles, role_assignments: danh tính và ReBAC/ABAC
  2. documents, document_versions, document_version_relations: tài liệu và phiên bản
  3. chunks: nội dung đã chia đoạn và embedding
  4. conversations, messages, message_sources: lịch sử hội thoại và citations
  5. audit_log, ingestion_jobs, authz_outbox: vận hành và audit

CLI:
    python -m app.db init     # tạo schema (idempotent)
    python -m app.db check    # kiểm tra PostgreSQL + OpenFGA
    python -m app.db reset    # XÓA và tạo lại schema (chỉ dev)
"""
from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any, Iterator

import httpx
import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from app.config import get_settings

log = logging.getLogger(__name__)

# Độ mật (0..3): đồng bộ với roles.max_classification.
CLASSIFICATION_LEVELS: dict[str, int] = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}
CLASSIFICATION_NAMES: dict[int, str] = {v: k for k, v in CLASSIFICATION_LEVELS.items()}
DOC_STATUSES = ("draft", "active", "superseded", "expired")
RELATION_TYPES = ("supersedes", "amends")
ALLOWED_DOC_TYPES = ("policy", "process", "guideline", "faq")

EXPECTED_TABLES = {
    "users",
    "organization_units",
    "memberships",
    "roles",
    "role_assignments",
    "documents",
    "document_versions",
    "document_version_relations",
    "chunks",
    "conversations",
    "messages",
    "message_sources",
    "audit_log",
    "ingestion_jobs",
    "authz_outbox",
}


# -----------------------------------------------------------------------------
# DDL Schema
# -----------------------------------------------------------------------------
def build_schema_sql(embedding_dim: int) -> str:
    """Load the canonical PostgreSQL schema from backend/db/schema.sql."""
    schema_file = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
    schema = schema_file.read_text(encoding="utf-8")
    if f"VECTOR({embedding_dim})" not in schema.upper():
        raise ValueError(
            f"EMBEDDING_DIM={embedding_dim} does not match vector dimension in {schema_file}"
        )
    return schema


RESET_SQL = """
DROP TABLE IF EXISTS authz_outbox CASCADE;
DROP TABLE IF EXISTS ingestion_jobs CASCADE;
DROP TABLE IF EXISTS audit_log CASCADE;
DROP TABLE IF EXISTS message_sources CASCADE;
DROP TABLE IF EXISTS messages CASCADE;
DROP TABLE IF EXISTS conversations CASCADE;
DROP TABLE IF EXISTS chunks CASCADE;
DROP TABLE IF EXISTS document_version_relations CASCADE;
DROP TABLE IF EXISTS document_versions CASCADE;
DROP TABLE IF EXISTS documents CASCADE;
DROP TABLE IF EXISTS role_assignments CASCADE;
DROP TABLE IF EXISTS roles CASCADE;
DROP TABLE IF EXISTS memberships CASCADE;
DROP TABLE IF EXISTS organization_units CASCADE;
DROP TABLE IF EXISTS users CASCADE;
"""


# -----------------------------------------------------------------------------
# Connection Pool (psycopg)
# -----------------------------------------------------------------------------
_pool: ConnectionPool | None = None


def _configure(conn: psycopg.Connection) -> None:
    register_vector(conn)


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        s = get_settings()
        _pool = ConnectionPool(
            s.database_url,
            min_size=s.db_pool_min,
            max_size=s.db_pool_max,
            configure=_configure,
            open=False,
        )
        _pool.open(wait=True, timeout=15)
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    with get_pool().connection() as conn:
        yield conn


# -----------------------------------------------------------------------------
# Schema Management
# -----------------------------------------------------------------------------
def init_schema(reset: bool = False) -> None:
    """Apply the canonical database schema without seeding mock identities."""
    s = get_settings()
    with psycopg.connect(s.database_url, autocommit=True) as conn:
        if reset:
            conn.execute(RESET_SQL, prepare=False)
        else:
            has_documents = conn.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public' AND table_name = 'documents'
                )
                """
            ).fetchone()[0]
            has_document_code = conn.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'documents'
                      AND column_name = 'document_code'
                )
                """
            ).fetchone()[0]
            if has_documents and not has_document_code:
                raise RuntimeError(
                    "Legacy database schema detected. Back up and migrate the existing "
                    "database before applying backend/db/schema.sql; use reset only in dev."
                )
        conn.execute(build_schema_sql(s.embedding_dim), prepare=False)
    log.info("Database schema ready (embedding_dim=%s)", s.embedding_dim)


# -----------------------------------------------------------------------------
# Write Audit Log (psycopg)
# -----------------------------------------------------------------------------
def write_audit(
    conn: psycopg.Connection,
    user_id: str,
    *,
    context_unit: str | None = None,
    question_hash: str | None = None,
    as_of_date: date | None = None,
    structured_query: dict | None = None,
    allowed_units: list[str] | None = None,
    max_classification: int | None = None,
    authz_decision: dict | None = None,
    retrieved_chunk_ids: list[str] | None = None,
    source_hashes: list[str] | None = None,
    outcome: str = "answered",
    abstain_reason: str | None = None,
    latency_ms: int | None = None,
    resolution: dict | None = None,
) -> None:
    """Write an append-only event using the normalized audit schema."""
    outcome_to_decision = {
        "answered": "allow",
        "abstained": "deny",
        "denied": "deny",
        "error": "failure",
    }
    request_id = (authz_decision or {}).get("request_id")
    try:
        from uuid import UUID

        request_id = UUID(str(request_id)) if request_id else None
    except ValueError:
        request_id = None

    metadata = {
        "context_unit": context_unit,
        "question_hash": question_hash,
        "as_of_date": as_of_date.isoformat() if as_of_date else None,
        "structured_query": structured_query,
        "allowed_units": allowed_units,
        "max_classification": max_classification,
        "authz_decision": authz_decision,
        "retrieved_chunk_ids": retrieved_chunk_ids or [],
        "source_hashes": source_hashes or [],
        "outcome": outcome,
        "abstain_reason": abstain_reason,
        "latency_ms": latency_ms,
        "resolution": resolution,
    }
    conn.execute(
        """
        INSERT INTO audit_log (
            request_id, actor_user_id, event_type, resource_type, resource_id,
            decision, reason_code, metadata
        )
        VALUES (
            %s,
            (SELECT user_id FROM users WHERE user_code = %s),
            %s, 'unit', %s, %s, %s, %s
        )
        """,
        (
            request_id,
            user_id,
            (structured_query or {}).get("type", "rag_query"),
            context_unit,
            outcome_to_decision.get(outcome, "failure"),
            abstain_reason,
            Jsonb(metadata),
        ),
    )


class Database:
    """Thin adapter used by the RAG pipeline.

    It keeps retrieval code decoupled from psycopg while still using the shared
    connection pool and schema in this module.
    """

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or get_settings().database_url

    def open(self) -> None:
        get_pool()

    def close(self) -> None:
        close_pool()

    def fetch_all(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with get_pool().connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params or {})
                return list(cur.fetchall())

    def execute(self, sql: str, params: Any = None) -> None:
        with get_pool().connection() as conn:
            conn.execute(sql, params)


# -----------------------------------------------------------------------------
# Health Check
# -----------------------------------------------------------------------------
def check_db() -> dict:
    with get_conn() as conn:
        conn.execute("SELECT 1")
        exts = {r[0] for r in conn.execute("SELECT extname FROM pg_extension")}
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
            )
        }
    missing_ext = {"vector", "unaccent", "btree_gist"} - exts
    missing_tables = EXPECTED_TABLES - tables
    ok = not missing_ext and not missing_tables
    return {
        "ok": ok,
        "missing_extensions": sorted(missing_ext),
        "missing_tables": sorted(missing_tables),
    }


def check_openfga() -> dict:
    s = get_settings()
    base = s.openfga_api_url.rstrip("/")
    try:
        r = httpx.get(f"{base}/healthz", timeout=5)
        healthy = r.status_code == 200
        stores = httpx.get(f"{base}/stores", timeout=5).json().get("stores", [])
    except httpx.HTTPError as e:
        return {"ok": False, "error": str(e)}
    configured = bool(s.openfga_store_id)
    store_found = (
        any(st["id"] == s.openfga_store_id for st in stores) if configured else None
    )
    return {
        "ok": healthy,
        "store_id_configured": configured,
        "store_found": store_found,
        "stores": len(stores),
    }


def check_all() -> bool:
    results = {}
    try:
        results["postgres"] = check_db()
    except Exception as e:  # noqa: BLE001
        results["postgres"] = {"ok": False, "error": str(e)}
    results["openfga"] = check_openfga()
    for name, res in results.items():
        print(f"[{'OK' if res['ok'] else 'FAIL'}] {name}: {res}")
    return all(r["ok"] for r in results.values())


def main(argv: list[str]) -> int:
    logging.basicConfig(level=get_settings().log_level)
    cmd = argv[1] if len(argv) > 1 else "check"
    try:
        if cmd == "init":
            init_schema()
            return 0 if check_all() else 1
        if cmd == "reset":
            if get_settings().app_env != "dev":
                print("reset chỉ được phép khi APP_ENV=dev")
                return 2
            init_schema(reset=True)
            return 0 if check_all() else 1
        if cmd == "check":
            return 0 if check_all() else 1
        print(__doc__)
        return 2
    finally:
        close_pool()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
