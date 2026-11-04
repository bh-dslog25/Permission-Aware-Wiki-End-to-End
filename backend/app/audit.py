"""Audit sink/query helpers for the MVP schema."""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from app.db import Database, get_conn, write_audit


class AuditLogger:
    def __init__(self, db: Database | None = None) -> None:
        self.db = db

    def record(
        self,
        *,
        request_id: str,
        actor_id: str,
        active_context: str,
        action: str,
        authorization_result: dict[str, Any],
        source_ids: list[str],
        source_hashes: list[str],
        details: dict[str, Any],
    ) -> None:
        question_hash = None
        if details.get("question"):
            question_hash = hashlib.sha256(str(details["question"]).encode("utf-8")).hexdigest()

        timings = details.get("timings") or {}
        latency = timings.get("total_ms")
        structured = details.get("structured_query")
        outcome = details.get("status") or "error"
        if outcome == "needs_clarification":
            outcome = "abstained"

        with get_conn() as conn:
            write_audit(
                conn,
                user_id=actor_id,
                context_unit=active_context,
                question_hash=question_hash,
                as_of_date=details.get("as_of"),
                structured_query=structured,
                allowed_units=authorization_result.get("allowed_units"),
                max_classification=authorization_result.get("max_classification"),
                authz_decision={
                    "request_id": request_id,
                    "action": action,
                    **authorization_result,
                },
                retrieved_chunk_ids=source_ids,
                source_hashes=source_hashes,
                outcome=outcome if outcome in {"answered", "abstained", "denied", "error"} else "error",
                abstain_reason=details.get("abstain_code"),
                latency_ms=int(latency) if isinstance(latency, (int, float)) else None,
                resolution={
                    "dropped": details.get("dropped", []),
                    "notes": details.get("resolution_notes", []),
                    "request_id": request_id,
                },
            )


def query_audit_logs(
    *,
    user_id: str | None = None,
    outcome: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    if user_id:
        clauses.append("u.user_code = %s")
        params.append(user_id)
    if outcome:
        clauses.append("al.metadata->>'outcome' = %s")
        params.append(outcome)
    if since:
        clauses.append("al.occurred_at >= %s")
        params.append(since)
    if until:
        clauses.append("al.occurred_at <= %s")
        params.append(until)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT al.audit_id, al.occurred_at, u.user_code, al.resource_id, al.metadata
        FROM audit_log al
        LEFT JOIN users u ON u.user_id = al.actor_user_id
        {where_sql}
        ORDER BY al.occurred_at DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    with get_conn() as conn:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    return [
        {
            "id": row[0],
            "ts": row[1],
            "user_id": row[2],
            "context_unit": row[3],
            "as_of_date": row[4].get("as_of_date"),
            "structured_query": row[4].get("structured_query"),
            "allowed_units": row[4].get("allowed_units"),
            "max_classification": row[4].get("max_classification"),
            "authz_decision": row[4].get("authz_decision"),
            "retrieved_chunk_ids": row[4].get("retrieved_chunk_ids"),
            "source_hashes": row[4].get("source_hashes"),
            "outcome": row[4].get("outcome"),
            "abstain_reason": row[4].get("abstain_reason"),
            "latency_ms": row[4].get("latency_ms"),
            "resolution": row[4].get("resolution"),
        }
        for row in rows
    ]
