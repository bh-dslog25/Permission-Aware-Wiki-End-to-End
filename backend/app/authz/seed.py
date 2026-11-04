"""Nạp quan hệ user và đơn vị đang có hiệu lực từ PostgreSQL vào OpenFGA.

Chạy:  python -m app.authz.seed     (từ thư mục backend/)
Yêu cầu: OpenFGA đang chạy (OPENFGA_API_URL) và `fga` CLI có trong PATH (hoặc đặt FGA_BIN)
         để đổi model.fga (DSL) sang JSON.
Lưu ý: seed XÓA và dựng lại store có tên OPENFGA_STORE_NAME.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

import httpx

from app.authz.policy_adapter import DEFAULT_STORE_NAME, find_store_id
from app.db import get_conn

MODEL_FILE = Path(__file__).with_name("model.fga")
BATCH = 50  # OpenFGA giới hạn số tuple mỗi lần ghi
log = logging.getLogger(__name__)


DEFAULT_AUTH_MODEL = {
    "schema_version": "1.1",
    "type_definitions": [
        {"type": "user"},
        {
            "type": "unit",
            "relations": {
                "child": {"this": {}},
                "assigned": {"this": {}},
                "member": {"this": {}},
                "viewer": {
                    "union": {
                        "child": [
                            {"computedUserset": {"relation": "member"}},
                            {
                                "tupleToUserset": {
                                    "tupleset": {"relation": "child"},
                                    "computedUserset": {"relation": "viewer"},
                                }
                            },
                        ]
                    }
                },
            },
            "metadata": {
                "relations": {
                    "child": {"directly_related_user_types": [{"type": "unit"}]},
                    "assigned": {"directly_related_user_types": [{"type": "user"}]},
                    "member": {"directly_related_user_types": [{"type": "user"}]},
                    "viewer": {"directly_related_user_types": []},
                }
            },
        },
    ],
}


def transform_model(path: Path = MODEL_FILE) -> dict:
    fga = os.getenv("FGA_BIN", "fga")
    try:
        out = subprocess.run(
            [fga, "model", "transform", "--file", str(path), "--output-format", "json"],
            capture_output=True,
            text=True,
            check=True,
        )
        return json.loads(out.stdout)
    except (FileNotFoundError, subprocess.SubprocessError):
        return DEFAULT_AUTH_MODEL


def build_tuples() -> list[dict]:
    """Build OpenFGA tuples only from active database records."""
    with get_conn() as conn:
        parent_rows = conn.execute(
            """
            SELECT child.unit_code, parent.unit_code
            FROM organization_units child
            JOIN organization_units parent ON parent.unit_id = child.parent_unit_id
            WHERE child.status = 'active' AND parent.status = 'active'
            """
        ).fetchall()
        memberships = conn.execute(
            """
            SELECT DISTINCT u.user_code, unit.unit_code
            FROM memberships m
            JOIN users u ON u.user_id = m.user_id
            JOIN organization_units unit ON unit.unit_id = m.unit_id
            WHERE u.status = 'active'
              AND m.status = 'active'
              AND unit.status = 'active'
              AND m.valid_from <= now()
              AND (m.valid_to IS NULL OR m.valid_to > now())
            """
        ).fetchall()

    tuples = [
        {"user": f"unit:{child}", "relation": "child", "object": f"unit:{parent}"}
        for child, parent in parent_rows
    ]
    tuples.extend(
        {"user": f"user:{user_code}", "relation": "assigned", "object": f"unit:{unit_code}"}
        for user_code, unit_code in memberships
    )
    return tuples


def process_outbox(api_url: str | None = None) -> dict[str, int]:
    api_url = (api_url or os.getenv("OPENFGA_API_URL", "http://localhost:8080")).rstrip("/")
    store_name = os.getenv("OPENFGA_STORE_NAME", DEFAULT_STORE_NAME)
    stats = {"completed": 0, "failed": 0}
    with httpx.Client(base_url=api_url, timeout=10.0) as http:
        store_id = find_store_id(http, store_name)
        if store_id is None:
            raise RuntimeError(f"OpenFGA store {store_name!r} does not exist; seed it first")
        models = http.get(
            f"/stores/{store_id}/authorization-models", params={"page_size": 1}
        )
        models.raise_for_status()
        model_rows = models.json().get("authorization_models", [])
        if not model_rows:
            raise RuntimeError("OpenFGA store has no authorization model")
        model_id = model_rows[0]["id"]

        while True:
            with get_conn() as conn, conn.transaction():
                row = conn.execute(
                    """
                    SELECT event_id, event_type, tuple_payload
                    FROM authz_outbox
                    WHERE status = 'pending'
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    break
                event_id, event_type, payload = row
                conn.execute(
                    "UPDATE authz_outbox SET status = 'processing', retry_count = retry_count + 1 WHERE event_id = %s",
                    (event_id,),
                )

            tuples = []
            if event_type == "user_membership_created":
                tuples = [payload]
                body = {"writes": {"tuple_keys": tuples}}
            elif event_type == "user_status_changed":
                tuples = [
                    {"user": payload["user"], "relation": "assigned", "object": f"unit:{unit}"}
                    for unit in payload["units"]
                ]
                body = (
                    {"writes": {"tuple_keys": tuples}}
                    if payload["status"] == "active"
                    else {"deletes": {"tuple_keys": tuples}}
                )
            elif event_type == "user_membership_revoked":
                body = {"deletes": {"tuple_keys": [payload]}}
            else:
                body = {}

            try:
                if not body:
                    raise ValueError(f"Unsupported authz outbox event {event_type!r}")
                response = http.post(
                    f"/stores/{store_id}/write",
                    json={"authorization_model_id": model_id, **body},
                )
                response.raise_for_status()
            except (httpx.HTTPError, ValueError) as exc:
                with get_conn() as conn:
                    conn.execute(
                        """
                        UPDATE authz_outbox
                        SET status = 'failed', last_error = %s
                        WHERE event_id = %s
                        """,
                        (str(exc), event_id),
                    )
                stats["failed"] += 1
                log.error("Failed to sync authz outbox event %s: %s", event_id, exc)
                continue

            with get_conn() as conn:
                conn.execute(
                    """
                    UPDATE authz_outbox
                    SET status = 'completed', last_error = NULL, processed_at = now()
                    WHERE event_id = %s
                    """,
                    (event_id,),
                )
            stats["completed"] += 1
    return stats


def run(api_url: str | None = None, store_name: str | None = None) -> str:
    api_url = (api_url or os.getenv("OPENFGA_API_URL", "http://localhost:8080")).rstrip("/")
    store_name = store_name or os.getenv("OPENFGA_STORE_NAME", DEFAULT_STORE_NAME)
    model = transform_model()
    tuples = build_tuples()

    with httpx.Client(base_url=api_url, timeout=10.0) as http:
        old = find_store_id(http, store_name)
        if old:
            http.delete(f"/stores/{old}").raise_for_status()
        r = http.post("/stores", json={"name": store_name})
        r.raise_for_status()
        store_id = r.json()["id"]

        r = http.post(f"/stores/{store_id}/authorization-models", json=model)
        r.raise_for_status()
        model_id = r.json()["authorization_model_id"]

        for i in range(0, len(tuples), BATCH):
            r = http.post(f"/stores/{store_id}/write", json={
                "authorization_model_id": model_id,
                "writes": {"tuple_keys": tuples[i:i + BATCH]},
            })
            r.raise_for_status()
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE authz_outbox
            SET status = 'completed', last_error = NULL, processed_at = now()
            WHERE status <> 'completed'
            """
        )
    print(f"OpenFGA store '{store_name}' = {store_id}, model = {model_id}, tuples = {len(tuples)}")
    return store_id


if __name__ == "__main__":
    run()
