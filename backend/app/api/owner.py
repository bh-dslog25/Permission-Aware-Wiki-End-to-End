"""Review and approval endpoints for Knowledge Owners.

Cho phép Knowledge Owner rà soát tài liệu unverified, đối chiếu với chính sách cấp trên,
phê duyệt hoặc ghi nhận xung đột kèm lý do/căn cứ điều khoản, lưu audit log
và đồng bộ trực tiếp vào documents và chunks.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.auth import get_persona, unit_name
from app.db import get_conn, write_audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/owner", tags=["owner"])


class ReviewSubmission(BaseModel):
    decision: Literal["confirmed", "conflicting", "unverified"]
    justification: str = Field(min_length=3, max_length=1000)
    clause: str = Field(default="", max_length=200)


def _verify_knowledge_owner(x_persona_id: str | None) -> Any:
    """Kiểm tra quyền Knowledge Owner ở backend.

    Chỉ người dùng có role hr_owner (hoặc security/admin) mới được truy cập portal này.
    """
    if not x_persona_id:
        raise HTTPException(
            status_code=401,
            detail="Yêu cầu đăng nhập tài khoản Knowledge Owner.",
        )
    persona = get_persona(x_persona_id)
    if persona.role not in {"hr_owner", "security"}:
        raise HTTPException(
            status_code=403,
            detail=f"Tài khoản {persona.name} (role: {persona.role}) không có quyền Knowledge Owner để duyệt tài liệu.",
        )
    return persona


@router.get("/pending-reviews")
def list_pending_reviews(
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
    status: Literal["pending", "reviewed", "all"] = Query(default="pending"),
) -> dict[str, Any]:
    """Danh sách các tài liệu cần rà soát hoặc tài liệu đã được duyệt, kèm chính sách đối chiếu và lịch sử duyệt."""
    owner = _verify_knowledge_owner(x_persona_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # Lọc theo tab:
            # - pending: unverified hoặc conflicting
            # - reviewed: confirmed
            # - all: tất cả
            if status == "pending":
                where_clause = "WHERE parent_conformance IN ('unverified', 'conflicting')"
            elif status == "reviewed":
                where_clause = "WHERE parent_conformance = 'confirmed'"
            else:
                where_clause = ""

            cur.execute(f"""
                SELECT d.document_code AS document_id, v.title, v.doc_type,
                       owner.unit_code AS owner_unit, v.scope, v.classification,
                       v.version_label AS version, v.valid_from, v.valid_to,
                       CASE v.status WHEN 'published' THEN 'active' ELSE v.status END AS status,
                       v.mandatory, v.approved, v.topic, v.parent_conformance,
                       v.source_path, v.updated_at, v.version_id
                FROM documents d
                JOIN document_versions v ON v.document_id = d.document_id
                JOIN organization_units owner ON owner.unit_id = v.owner_unit_id
                {where_clause}
                ORDER BY v.updated_at DESC, d.document_code ASC
            """)
            cols = [desc[0] for desc in cur.description]
            docs = [dict(zip(cols, row)) for row in cur.fetchall()]

            # Lấy lịch sử duyệt mới nhất từ audit_log cho từng tài liệu
            cur.execute("""
                SELECT DISTINCT ON (al.metadata->'structured_query'->>'document_id')
                    al.metadata->'structured_query'->>'document_id' AS doc_id,
                    u.user_code AS user_id,
                    al.occurred_at AS ts,
                    al.metadata->'structured_query'->>'new_conformance' AS decision,
                    al.metadata->'structured_query'->>'justification' AS justification,
                    al.metadata->'structured_query'->>'clause' AS clause
                FROM audit_log al
                LEFT JOIN users u ON u.user_id = al.actor_user_id
                WHERE al.metadata->'structured_query'->>'type' = 'knowledge_owner_review'
                ORDER BY al.metadata->'structured_query'->>'document_id', al.occurred_at DESC
            """)
            audit_cols = [desc[0] for desc in cur.description]
            review_map = {r[0]: dict(zip(audit_cols, r)) for r in cur.fetchall()}

            items = []
            for doc in docs:
                topic = doc.get("topic")
                owner_unit = doc.get("owner_unit")
                doc_id = doc["document_id"]

                # Gắn lịch sử phê duyệt nếu có
                doc["last_review"] = review_map.get(doc_id)

                # Lấy chunks nội dung của document này
                cur.execute("""
                    SELECT chunk_id, chunk_index, section_path, content
                    FROM chunks
                    WHERE version_id = %s AND is_active
                    ORDER BY chunk_index ASC
                """, (doc["version_id"],))
                chunk_cols = [desc[0] for desc in cur.description]
                chunks = [dict(zip(chunk_cols, r)) for r in cur.fetchall()]

                # Tìm tài liệu cấp trên cùng topic để đối chiếu
                parent_policy = None
                if topic:
                    cur.execute("""
                        SELECT parent_doc.document_code AS document_id, parent_v.title,
                               parent_owner.unit_code AS owner_unit, parent_v.scope,
                               parent_v.version_label AS version, parent_v.mandatory,
                               parent_v.topic, parent_v.version_id
                        FROM document_versions parent_v
                        JOIN documents parent_doc ON parent_doc.document_id = parent_v.document_id
                        JOIN organization_units parent_owner ON parent_owner.unit_id = parent_v.owner_unit_id
                        WHERE parent_v.topic = %s
                          AND parent_doc.document_code <> %s
                          AND parent_v.scope IN ('group', 'company')
                        ORDER BY parent_v.mandatory DESC, parent_v.valid_from DESC
                        LIMIT 1
                    """, (topic, doc_id))
                    p_row = cur.fetchone()
                    if p_row:
                        p_cols = [desc[0] for desc in cur.description]
                        p_dict = dict(zip(p_cols, p_row))
                        cur.execute("""
                            SELECT chunk_id, chunk_index, section_path, content
                            FROM chunks
                            WHERE version_id = %s AND is_active
                            ORDER BY chunk_index ASC
                            LIMIT 5
                        """, (p_dict["version_id"],))
                        p_chunk_cols = [desc[0] for desc in cur.description]
                        p_dict["chunks"] = [dict(zip(p_chunk_cols, r)) for r in cur.fetchall()]
                        p_dict["owner_unit_name"] = unit_name(p_dict["owner_unit"])
                        parent_policy = p_dict

                doc["owner_unit_name"] = unit_name(owner_unit)
                doc["chunks"] = chunks
                doc["parent_policy"] = parent_policy
                items.append(doc)

            # Thống kê nhanh số lượng
            cur.execute("""
                SELECT
                    count(*) FILTER (WHERE parent_conformance IN ('unverified', 'conflicting')) as pending_count,
                    count(*) FILTER (WHERE parent_conformance = 'confirmed') as reviewed_count,
                    count(*) as total_count
                FROM document_versions
            """)
            counts = cur.fetchone()

            return {
                "owner": {
                    "id": owner.user_id,
                    "name": owner.name,
                    "role": owner.role,
                },
                "stats": {
                    "pending": counts[0] if counts else 0,
                    "reviewed": counts[1] if counts else 0,
                    "total": counts[2] if counts else 0,
                },
                "items": items,
            }


@router.post("/reviews/{document_id}")
def review_document(
    document_id: str,
    payload: ReviewSubmission,
    x_persona_id: str | None = Header(default=None, alias="X-Persona-Id"),
) -> dict[str, Any]:
    """Knowledge Owner thực hiện rà soát và phê duyệt tài liệu."""
    owner = _verify_knowledge_owner(x_persona_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT v.version_id, d.document_code, v.title, owner.unit_code,
                       v.status, v.parent_conformance
                FROM documents d
                JOIN document_versions v ON v.document_id = d.document_id
                JOIN organization_units owner ON owner.unit_id = v.owner_unit_id
                WHERE d.document_code = %s
                ORDER BY v.updated_at DESC
                LIMIT 1
            """, (document_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Không tìm thấy tài liệu")

            version_id, title, owner_unit, current_status, prev_conformance = row[0], row[2], row[3], row[4], row[5]

            # 1. Cập nhật parent_conformance của document
            cur.execute("""
                UPDATE document_versions
                SET parent_conformance = %s,
                    approved = (%s = 'confirmed'),
                    approved_by = CASE WHEN %s = 'confirmed'
                        THEN (SELECT user_id FROM users WHERE user_code = %s)
                        ELSE NULL END,
                    approved_at = CASE WHEN %s = 'confirmed' THEN now() ELSE NULL END,
                    updated_at = now()
                WHERE version_id = %s
            """, (
                payload.decision, payload.decision, payload.decision,
                owner.user_id, payload.decision, version_id,
            ))

            # 2. Ghi nhận nhật ký audit append-only
            write_audit(
                conn,
                user_id=owner.user_id,
                context_unit=owner_unit,
                question_hash=None,
                as_of_date=date.today(),
                structured_query={
                    "type": "knowledge_owner_review",
                    "document_id": document_id,
                    "title": title,
                    "previous_conformance": prev_conformance,
                    "new_conformance": payload.decision,
                    "justification": payload.justification,
                    "clause": payload.clause,
                },
                allowed_units=[owner_unit],
                max_classification=None,
                authz_decision={
                    "actor_role": owner.role,
                    "action": "document_review",
                    "decision": payload.decision,
                },
                retrieved_chunk_ids=[],
                source_hashes=[],
                outcome="answered",
                abstain_reason=None,
                latency_ms=0,
                resolution={
                    "reviewer_id": owner.user_id,
                    "reviewer_name": owner.name,
                    "justification": payload.justification,
                    "clause": payload.clause,
                },
            )

        conn.commit()

    logger.info(
        "Knowledge Owner %s reviewed %s: %s (clause: %s, note: %s)",
        owner.user_id, document_id, payload.decision, payload.clause, payload.justification
    )

    return {
        "status": "success",
        "document_id": document_id,
        "new_conformance": payload.decision,
        "reviewer": owner.name,
        "message": f"Tài liệu {document_id} đã được cập nhật trạng thái '{payload.decision}'.",
    }
