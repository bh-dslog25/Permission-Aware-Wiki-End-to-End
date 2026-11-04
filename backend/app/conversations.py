"""Persistent conversation history scoped to a demo persona."""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from app.db import get_conn


def list_conversations(user_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT c.conversation_id, c.title, c.context_id, c.created_at, c.updated_at
            FROM conversations c
            JOIN users u ON u.user_id = c.user_id
            WHERE u.user_code = %s
            ORDER BY updated_at DESC
            LIMIT %s
            """,
            (user_id, limit),
        ).fetchall()
    return [
        {
            "id": row[0],
            "title": row[1],
            "context_id": row[2],
            "created_at": row[3],
            "updated_at": row[4],
        }
        for row in rows
    ]


def get_conversation(user_id: str, conversation_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT c.conversation_id, c.title, c.context_id, c.created_at, c.updated_at
            FROM conversations c
            JOIN users u ON u.user_id = c.user_id
            WHERE u.user_code = %s AND c.conversation_id = %s::uuid
            """,
            (user_id, conversation_id),
        ).fetchone()
    if row is None:
        return None
    with get_conn() as conn:
        messages = conn.execute(
            """
            SELECT metadata->'exchange'
            FROM messages
            WHERE conversation_id = %s::uuid AND role = 'assistant'
            ORDER BY created_at, message_id
            """,
            (conversation_id,),
        ).fetchall()
    return {
        "id": row[0],
        "title": row[1],
        "context_id": row[2],
        "messages": [message[0] for message in messages],
        "created_at": row[3],
        "updated_at": row[4],
    }


def append_exchange(
    *,
    user_id: str,
    conversation_id: str,
    context_id: str,
    question: str,
    answer: dict[str, Any],
) -> None:
    entry = {"id": answer["request_id"], "question": question, "answer": answer}
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO conversations (conversation_id, user_id, title, context_id)
            SELECT %s::uuid, user_id, %s, %s
            FROM users WHERE user_code = %s AND status = 'active'
            ON CONFLICT (conversation_id) DO UPDATE
            SET title = COALESCE(conversations.title, EXCLUDED.title),
                context_id = EXCLUDED.context_id,
                updated_at = now()
            WHERE conversations.user_id = EXCLUDED.user_id
            RETURNING conversation_id
            """,
            (
                conversation_id,
                question[:120],
                context_id,
                user_id,
            ),
        )
        if cursor.fetchone() is None:
            raise RuntimeError("Không thể lưu hội thoại cho tài khoản hiện tại")
        conn.execute(
            """
            INSERT INTO messages (conversation_id, role, content, metadata)
            VALUES (%s::uuid, 'assistant', %s, %s)
            """,
            (
                conversation_id,
                str(answer.get("answer", "")),
                Jsonb({"exchange": entry}),
            ),
        )
