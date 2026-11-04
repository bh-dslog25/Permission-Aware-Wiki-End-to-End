"""Nạp tài liệu và tạo chỉ mục cho MVP.

Quy trình:
    Markdown + front matter ─▶ validate (default-deny) ─▶ chunk theo Điều/Khoản
        ─▶ embedding (BGE-M3 hoặc Fake) ─▶ pgvector (1 transaction / tài liệu)

Nguyên tắc:
  - Fail-closed: tài liệu lỗi không bao giờ nạp vào DB. Nếu bản cũ đã có trong DB, đặt approved=false.
  - Bỏ qua tài liệu không đổi (so khớp content_hash).
  - Tối giản: không advisory lock, không quarantine table, không search CLI không phân quyền.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import math
import random
import re
import sys
import time
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any, Protocol, Sequence

import yaml
from psycopg.types.json import Jsonb

from app.config import Settings, get_settings
from app.db import (
    ALLOWED_DOC_TYPES,
    CLASSIFICATION_LEVELS,
    DOC_STATUSES,
    get_conn,
    init_schema,
)

log = logging.getLogger("ingest")

MAX_FILE_BYTES = 2_000_000

REQUIRED_FIELDS = (
    "document_id", "title", "owner_unit", "scope", "doc_type", "version",
    "effective_from", "status", "classification", "mandatory",
)
OPTIONAL_FIELDS = ("logical_document_id", "effective_to", "topic", "parent_conformance", "policy_values", "supersedes", "amends")

ALLOWED_STATUS = frozenset(DOC_STATUSES)
ALLOWED_DOC_TYPES_SET = frozenset(ALLOWED_DOC_TYPES)
ALLOWED_CONFORMANCE = frozenset({"confirmed", "unverified", "conflicting"})

_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FM_RE = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
ARTICLE_RE = re.compile(r"^##[ \t]+Điều[ \t]+(\d{1,3})[ \t]*[.:–-][ \t]*(\S.*?)[ \t]*$", re.IGNORECASE)
CLAUSE_RE = re.compile(r"^(\d{1,3})[.)][ \t]+\S")
HEADING_RE = re.compile(r"^(#{1,2})[ \t]+\S")


# ════════════════════════════════════════════════════════════════════════════
# 1. Kiểu dữ liệu tối giản
# ════════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class Issue:
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


@dataclass
class ParsedDocument:
    path: Path
    source_path: str
    content_hash: str = ""
    doc_id: str = ""
    meta: dict[str, Any] | None = None
    chunks: list[dict[str, Any]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues


# ════════════════════════════════════════════════════════════════════════════
# 2. Tham chiếu tổ chức từ database
# ════════════════════════════════════════════════════════════════════════════
def load_organization_reference() -> dict[str, Any]:
    with get_conn() as conn:
        units = conn.execute(
            "SELECT unit_code, unit_type FROM organization_units WHERE status = 'active'"
        ).fetchall()
        if not units:
            raise RuntimeError("Chưa có organization units trong database")
    return {
        "unit_ids": frozenset(unit_code for unit_code, _ in units),
        "unit_types": {unit_code: unit_type for unit_code, unit_type in units},
        "classification_rank": CLASSIFICATION_LEVELS,
    }


# ════════════════════════════════════════════════════════════════════════════
# 3. Parse front matter & validate metadata (default-deny)
# ════════════════════════════════════════════════════════════════════════════
def normalize_text(s: str) -> str:
    s = s.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", s)


def split_front_matter(text: str) -> tuple[dict[str, Any] | None, str, int, list[Issue]]:
    m = _FM_RE.match(text)
    if not m:
        return None, text, 1, [Issue("E_NO_FRONT_MATTER", "Không tìm thấy front matter '---' ở đầu file")]
    body_first_line = text[: m.end()].count("\n") + 1
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as exc:
        return None, text[m.end():], body_first_line, [Issue("E_YAML_PARSE", f"Front matter không hợp lệ: {exc}")]
    if not isinstance(data, dict):
        return None, text[m.end():], body_first_line, [Issue("E_YAML_PARSE", "Front matter phải là mapping")]
    return data, text[m.end():], body_first_line, []


def _parse_date(value: Any, name: str, issues: list[Issue]) -> date | None:
    if isinstance(value, datetime):
        issues.append(Issue("E_BAD_DATE", f"{name}: chỉ nhận ngày (YYYY-MM-DD), không nhận ngày-giờ"))
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    issues.append(Issue("E_BAD_DATE", f"{name}: phải là ngày ISO YYYY-MM-DD, nhận được {value!r}"))
    return None


def _ref_list(raw: Any, name: str, issues: list[Issue]) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(x, str) and _ID_RE.match(x) for x in raw):
        issues.append(Issue("E_BAD_REF", f"{name}: phải là danh sách document_id hợp lệ"))
        return []
    return list(raw)


def validate_meta(raw: dict[str, Any], stem: str, org: dict[str, Any]) -> tuple[dict[str, Any] | None, list[Issue], list[Issue]]:
    issues: list[Issue] = []
    warnings: list[Issue] = []

    for name in REQUIRED_FIELDS:
        v = raw.get(name)
        if v is None or (isinstance(v, str) and not v.strip()):
            issues.append(Issue("E_MISSING_FIELD", f"Thiếu trường bắt buộc: {name}"))
    if issues:
        return None, issues, warnings

    def s(name: str) -> str:
        return str(raw[name]).strip()

    # Document ID: ưu tiên front matter, dự phòng bằng stem file
    doc_id = s("document_id") or stem
    if not _ID_RE.match(doc_id) or len(doc_id) > 80:
        issues.append(Issue("E_BAD_ID", f"document_id không hợp lệ: {doc_id!r}"))
    elif doc_id != stem:
        issues.append(Issue("E_ID_MISMATCH", f"document_id '{doc_id}' phải trùng tên file '{stem}.md'"))

    if s("owner_unit") not in org["unit_ids"]:
        issues.append(Issue("E_UNKNOWN_UNIT", f"owner_unit='{s('owner_unit')}' không có trong database"))
    elif s("scope") != org["unit_types"].get(s("owner_unit")):
        issues.append(Issue(
            "E_SCOPE_MISMATCH",
            f"scope='{s('scope')}' không khớp loại đơn vị '{s('owner_unit')}' ({org['unit_types'].get(s('owner_unit'))})",
        ))

    if s("doc_type") not in ALLOWED_DOC_TYPES_SET:
        issues.append(Issue("E_BAD_ENUM", f"doc_type '{s('doc_type')}' không thuộc {sorted(ALLOWED_DOC_TYPES_SET)}"))
    if s("status") not in ALLOWED_STATUS:
        issues.append(Issue("E_BAD_ENUM", f"status '{s('status')}' không thuộc {sorted(ALLOWED_STATUS)}"))

    # Classification: chuyển tên chuỗi sang số nguyên 0..3
    class_str = s("classification")
    if class_str not in org["classification_rank"]:
        issues.append(Issue("E_BAD_ENUM", f"classification '{class_str}' không hợp lệ"))
        class_rank = 3
    else:
        class_rank = org["classification_rank"][class_str]

    if not isinstance(raw["mandatory"], bool):
        issues.append(Issue("E_BAD_TYPE", f"mandatory phải là boolean, nhận được {raw['mandatory']!r}"))

    eff_from = _parse_date(raw["effective_from"], "effective_from", issues)
    eff_to = _parse_date(raw["effective_to"], "effective_to", issues) if raw.get("effective_to") is not None else None
    if eff_from and eff_to and eff_to < eff_from:
        issues.append(Issue("E_DATE_RANGE", f"effective_to ({eff_to}) sớm hơn effective_from ({eff_from})"))

    supersedes = _ref_list(raw.get("supersedes"), "supersedes", issues)
    amends = _ref_list(raw.get("amends"), "amends", issues)

    # Validate topic & parent_conformance (cần cho resolution ở Bước 6)
    topic = raw.get("topic")
    if topic is not None and not isinstance(topic, str):
        issues.append(Issue("E_BAD_TYPE", "topic phải là chuỗi"))
    topic_str = topic.strip() if isinstance(topic, str) and topic.strip() else None

    parent_conformance = raw.get("parent_conformance", "unverified")
    if parent_conformance not in ALLOWED_CONFORMANCE:
        issues.append(Issue("E_BAD_ENUM", f"parent_conformance '{parent_conformance}' không hợp lệ"))

    if issues:
        return None, issues, warnings

    return (
        {
            "document_id": doc_id,
            "title": s("title"),
            "owner_unit": s("owner_unit"),
            "scope": s("scope"),
            "doc_type": s("doc_type"),
            "classification": class_rank,
            "version": s("version"),
            "effective_from": eff_from,
            "effective_to": eff_to,
            "status": s("status"),
            "mandatory": raw["mandatory"],
            "topic": topic_str,
            "parent_conformance": parent_conformance,
            "supersedes": supersedes,
            "amends": amends,
        },
        issues,
        warnings,
    )


def validate_status(meta: dict[str, Any], today: date) -> tuple[list[Issue], list[Issue]]:
    issues: list[Issue] = []
    warnings: list[Issue] = []
    status = meta["status"]
    eff_from = meta.get("effective_from")
    eff_to = meta.get("effective_to")

    if status in {"superseded", "expired"}:
        if eff_to is None:
            issues.append(Issue("E_STATUS_INCONSISTENT", f"status '{status}' bắt buộc có effective_to"))
        elif eff_to >= today:
            warnings.append(Issue("W_STATUS_EARLY", f"status '{status}' nhưng effective_to ({eff_to}) chưa tới"))
    if status == "active":
        if eff_to is not None and eff_to < today:
            issues.append(Issue("E_STATUS_INCONSISTENT", f"status 'active' nhưng đã hết hiệu lực từ {eff_to}"))
        if eff_from and eff_from > today:
            warnings.append(Issue("W_FUTURE_EFFECTIVE", f"status 'active' nhưng chỉ có hiệu lực từ {eff_from}"))
    return issues, warnings


# ════════════════════════════════════════════════════════════════════════════
# 4. Chunking theo Điều / Khoản
# ════════════════════════════════════════════════════════════════════════════
def _trim_blank(lines: list[str]) -> list[str]:
    out: list[str] = []
    for t in lines:
        if not t.strip():
            if out and out[-1]:
                out.append("")
            continue
        out.append(t)
    while out and not out[-1]:
        out.pop()
    return out


def _chunk_article(
    doc_id: str,
    art_num: int,
    art_title: str,
    lines: list[str],
    max_chars: int,
) -> list[dict[str, Any]]:
    intro: list[str] = []
    clauses: list[tuple[int, list[str]]] = []
    for t in lines:
        m = CLAUSE_RE.match(t)
        if m:
            clauses.append((int(m.group(1)), [t]))
        elif clauses:
            clauses[-1][1].append(t)
        else:
            intro.append(t)

    full_text = "\n".join(lines).strip()
    if len(full_text) <= max_chars or len(clauses) < 2:
        return [{
            "chunk_id": f"{doc_id}#d{art_num:02d}",
            "section_path": f"Điều {art_num}. {art_title}",
            "text": full_text,
        }]

    # Tách theo Khoản khi Điều vượt quá max_chars
    chunks = []
    for i, (c_num, c_lines) in enumerate(clauses, start=1):
        content = "\n".join(c_lines).strip()
        chunks.append({
            "chunk_id": f"{doc_id}#d{art_num:02d}.k{c_num}",
            "section_path": f"Điều {art_num} > Khoản {c_num}",
            "text": content,
        })
    return chunks


def chunk_body(
    doc_id: str,
    body: str,
    max_chars: int,
) -> tuple[list[dict[str, Any]], list[Issue], list[Issue]]:
    errors: list[Issue] = []
    warnings: list[Issue] = []
    articles: list[tuple[int, str, list[str]]] = []
    curr_art: tuple[int, str, list[str]] | None = None

    for line in body.split("\n"):
        m = ARTICLE_RE.match(line)
        if m:
            curr_art = (int(m.group(1)), m.group(2).strip(), [])
            articles.append(curr_art)
            continue
        h = HEADING_RE.match(line)
        if h and h.group(1) == "#" and not articles:
            continue  # H1 document title
        if curr_art is not None:
            curr_art[2].append(line.rstrip())

    if not articles:
        errors.append(Issue("E_NO_ARTICLES", "Không có mục '## Điều N. Tên điều' nào"))
        return [], errors, warnings

    chunks: list[dict[str, Any]] = []
    seen: set[int] = set()
    for art_num, art_title, lines in articles:
        if art_num in seen:
            errors.append(Issue("E_DUP_ARTICLE", f"Điều {art_num} bị trùng lặp"))
            continue
        seen.add(art_num)
        trimmed = _trim_blank(lines)
        if not trimmed:
            errors.append(Issue("E_EMPTY_ARTICLE", f"Điều {art_num} không có nội dung"))
            continue
        chunks.extend(_chunk_article(doc_id, art_num, art_title, trimmed, max_chars))

    for idx, c in enumerate(chunks):
        c["chunk_index"] = idx
    return chunks, errors, warnings


# ════════════════════════════════════════════════════════════════════════════
# 5. Parse tài liệu và kiểm tra liên tài liệu
# ════════════════════════════════════════════════════════════════════════════
def parse_document(path: Path, root: Path, org: dict[str, Any], *, max_chars: int, today: date) -> ParsedDocument:
    pd = ParsedDocument(path=path, source_path=path.relative_to(root).as_posix(), doc_id=path.stem)
    raw = path.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        pd.issues.append(Issue("E_TOO_LARGE", f"File vượt giới hạn {MAX_FILE_BYTES} bytes"))
        return pd
    try:
        text = normalize_text(raw.decode("utf-8"))
    except UnicodeDecodeError:
        pd.issues.append(Issue("E_ENCODING", "File không phải UTF-8"))
        return pd
    pd.content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    fm, body, _, fm_issues = split_front_matter(text)
    pd.issues += fm_issues
    if fm is None:
        return pd

    meta, m_issues, m_warn = validate_meta(fm, path.stem, org)
    pd.issues += m_issues
    pd.warnings += m_warn
    if meta:
        pd.doc_id = meta["document_id"]
        s_issues, s_warn = validate_status(meta, today)
        pd.issues += s_issues
        pd.warnings += s_warn

    chunks, c_issues, c_warn = chunk_body(pd.doc_id, body, max_chars)
    pd.issues += c_issues
    pd.warnings += c_warn
    pd.meta, pd.chunks = meta, chunks
    return pd


def validate_corpus(docs: list[ParsedDocument], today: date) -> None:
    by_id: dict[str, list[ParsedDocument]] = defaultdict(list)
    for d in docs:
        if d.doc_id:
            by_id[d.doc_id].append(d)

    for did, group in by_id.items():
        if len(group) > 1:
            where = ", ".join(sorted(g.source_path for g in group))
            for g in group:
                g.issues.append(Issue("E_DUP_ID", f"document_id '{did}' bị trùng ở: {where}"))

    known = set(by_id)
    for d in docs:
        if not d.meta:
            continue
        for ref in (*d.meta.get("supersedes", []), *d.meta.get("amends", [])):
            if ref == d.doc_id:
                d.issues.append(Issue("E_SELF_REF", "Tài liệu không thể supersedes/amends chính nó"))
            elif ref not in known:
                d.issues.append(Issue("E_DANGLING_REF", f"Tham chiếu tới document_id không tồn tại: '{ref}'"))


def scan_documents(settings: Settings, *, today: date | None = None, max_chars: int | None = None) -> list[ParsedDocument]:
    root = settings.documents_dir
    if not root.is_dir():
        raise FileNotFoundError(f"Không tìm thấy thư mục tài liệu: {root}")
    org = load_organization_reference()
    today = today or date.today()
    files = sorted(p for p in root.rglob("*.md") if not p.name.startswith("_") and p.name.lower() != "readme.md")
    docs = [parse_document(p, root, org, max_chars=max_chars or settings.max_chunk_chars, today=today) for p in files]
    validate_corpus(docs, today)
    return docs


# ════════════════════════════════════════════════════════════════════════════
# 6. Embedding
# ════════════════════════════════════════════════════════════════════════════
class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class BgeM3Embedder:
    def __init__(self, model_name: str, dim: int, batch_size: int, device: str | None):
        self.name, self.dim = model_name, dim
        self._batch, self._device = batch_size, device
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            log.info("Đang tải model embedding %s ...", self.name)
            model = SentenceTransformer(self.name, device=self._device)
            model.max_seq_length = 1024
            actual = model.get_sentence_embedding_dimension()
            if actual != self.dim:
                raise RuntimeError(f"Model trả về {actual} chiều nhưng EMBEDDING_DIM={self.dim}")
            self._model = model
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vecs = self._load().encode(list(texts), batch_size=self._batch, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vecs]


class FakeEmbedder:
    def __init__(self, dim: int):
        self.name, self.dim = "fake-hash-embedder", dim

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for t in texts:
            rng = random.Random(int.from_bytes(hashlib.sha256(t.encode("utf-8")).digest()[:8], "big"))
            v = [rng.gauss(0.0, 1.0) for _ in range(self.dim)]
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


def embedding_text(meta: dict[str, Any], chunk: dict[str, Any]) -> str:
    return f"{meta['title']}\n{chunk['section_path']}\n{chunk['text']}"


# ════════════════════════════════════════════════════════════════════════════
# 7. Persistence (ghi PostgreSQL trong 1 transaction)
# ════════════════════════════════════════════════════════════════════════════
_UPSERT_DOC = """
INSERT INTO documents (document_code)
VALUES (%(document_code)s)
ON CONFLICT (document_code) DO NOTHING;
"""

_INSERT_RELATION = """
INSERT INTO document_version_relations (
    source_version_id, target_version_id, relation_type
)
SELECT source.version_id, target.version_id, %(rel)s
FROM document_versions source
JOIN documents source_doc ON source_doc.document_id = source.document_id
JOIN document_versions target ON target.version_label = %(target_version)s
JOIN documents target_doc ON target_doc.document_id = target.document_id
WHERE source_doc.document_code = %(source)s
  AND source.version_label = %(source_version)s
  AND target_doc.document_code = %(target)s
ON CONFLICT DO NOTHING;
"""

_INSERT_CHUNK = """
INSERT INTO chunks (
    chunk_id, version_id, chunk_index, section_path, content, embedding
)
VALUES (
    %(chunk_id)s, %(version_id)s, %(chunk_index)s, %(section_path)s, %(content)s, %(embedding)s
)
ON CONFLICT (version_id, chunk_index) DO UPDATE SET
    section_path = EXCLUDED.section_path,
    content = EXCLUDED.content,
    embedding = EXCLUDED.embedding,
    is_active = TRUE;
"""


def _persist_document(
    conn: Any,
    pd: ParsedDocument,
    vectors: list[list[float]],
) -> None:
    from pgvector import Vector

    m = pd.meta
    assert m is not None
    doc_id = m["document_id"]
    # "approved" means the metadata/content is owner-approved for retrieval.
    # Historical versions still need to be retrievable for as-of questions; draft is excluded.
    is_approved = m["status"] != "draft"

    chunk_rows = [
        {
            "chunk_id": uuid.uuid5(uuid.NAMESPACE_URL, f"{doc_id}:{m['version']}:{c['chunk_index']}"),
            "chunk_index": c["chunk_index"],
            "section_path": c["section_path"],
            "content": c["text"],
            "embedding": Vector(vec),
        }
        for c, vec in zip(pd.chunks, vectors)
    ]

    with conn.transaction():
        conn.execute(_UPSERT_DOC, {"document_code": doc_id})
        document_id = conn.execute(
            "SELECT document_id FROM documents WHERE document_code = %s", (doc_id,)
        ).fetchone()[0]
        owner_unit_id = conn.execute(
            "SELECT unit_id FROM organization_units WHERE unit_code = %s",
            (m["owner_unit"],),
        ).fetchone()
        if owner_unit_id is None:
            raise ValueError(f"Unknown owner unit {m['owner_unit']!r}")

        valid_from = datetime.combine(m["effective_from"], datetime_time.min, timezone.utc)
        valid_to = (
            datetime.combine(m["effective_to"], datetime_time.max, timezone.utc)
            if m["effective_to"] else None
        )
        stored_status = "published" if m["status"] == "active" else m["status"]
        scope_type = "organization" if m["scope"] in {"group", "organization"} else "unit"
        version_row = conn.execute(
            """
            INSERT INTO document_versions (
                document_id, version_label, title, doc_type, owner_unit_id, scope,
                scope_type, classification, status, valid_from, valid_to, mandatory,
                topic, parent_conformance, policy_values, approved, source_path,
                source_object_key, content_hash
            )
            VALUES (
                %(document_id)s, %(version)s, %(title)s, %(doc_type)s, %(owner_unit_id)s,
                %(scope)s, %(scope_type)s, %(classification)s, %(status)s, %(valid_from)s,
                %(valid_to)s, %(mandatory)s, %(topic)s, %(parent_conformance)s,
                %(policy_values)s, %(approved)s, %(source_path)s, %(source_object_key)s,
                %(content_hash)s
            )
            ON CONFLICT (document_id, version_label) DO UPDATE SET
                title = EXCLUDED.title,
                doc_type = EXCLUDED.doc_type,
                owner_unit_id = EXCLUDED.owner_unit_id,
                scope = EXCLUDED.scope,
                scope_type = EXCLUDED.scope_type,
                classification = EXCLUDED.classification,
                status = EXCLUDED.status,
                valid_from = EXCLUDED.valid_from,
                valid_to = EXCLUDED.valid_to,
                mandatory = EXCLUDED.mandatory,
                topic = EXCLUDED.topic,
                parent_conformance = EXCLUDED.parent_conformance,
                policy_values = EXCLUDED.policy_values,
                approved = EXCLUDED.approved,
                source_path = EXCLUDED.source_path,
                source_object_key = EXCLUDED.source_object_key,
                content_hash = EXCLUDED.content_hash,
                updated_at = now()
            RETURNING version_id
            """,
            {
                "document_id": document_id,
                "version": m["version"],
                "owner_unit_id": owner_unit_id[0],
                "scope_type": scope_type,
                "status": stored_status,
                "valid_from": valid_from,
                "valid_to": valid_to,
                "policy_values": Jsonb(m.get("policy_values") or {}),
                "source_object_key": pd.source_path,
            },
        ).fetchone()[0]
        conn.execute("UPDATE chunks SET is_active = FALSE WHERE version_id = %s", (version_row,))
        for row in chunk_rows:
            row["version_id"] = version_row
        with conn.cursor() as cur:
            cur.executemany(_INSERT_CHUNK, chunk_rows)


def _persist_relations(conn: Any, docs: Sequence[ParsedDocument]) -> None:
    """Cập nhật quan hệ supersedes / amends sau khi TẤT CẢ documents đã tồn tại trong DB."""
    with conn.transaction():
        for d in docs:
            m = d.meta
            if not m:
                continue
            doc_id = m["document_id"]
            conn.execute(
                """
                DELETE FROM document_version_relations
                WHERE source_version_id = (
                    SELECT v.version_id FROM document_versions v
                    JOIN documents d ON d.document_id = v.document_id
                    WHERE d.document_code = %s AND v.version_label = %s
                )
                """,
                (doc_id, m["version"]),
            )
            for sup in m.get("supersedes", []):
                conn.execute(
                    _INSERT_RELATION,
                    {
                        "source": doc_id,
                        "source_version": m["version"],
                        "target": sup,
                        "target_version": _document_version(docs, sup),
                    },
                )
            for amd in m.get("amends", []):
                conn.execute(
                    _INSERT_RELATION,
                    {
                        "source": doc_id,
                        "source_version": m["version"],
                        "target": amd,
                        "target_version": _document_version(docs, amd),
                    },
                )


def _document_version(docs: Sequence[ParsedDocument], document_id: str) -> str:
    for doc in docs:
        if doc.meta and doc.meta["document_id"] == document_id:
            return doc.meta["version"]
    raise ValueError(f"Unknown document reference {document_id!r}")


# ════════════════════════════════════════════════════════════════════════════
# 8. Điều phối Ingest
# ════════════════════════════════════════════════════════════════════════════
def run_ingest(
    settings: Settings,
    *,
    use_fake: bool = False,
    dry_run: bool = False,
    force: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    docs = scan_documents(settings, today=today)
    accepted = [d for d in docs if d.ok]
    rejected = [d for d in docs if not d.ok]

    for d in rejected:
        log.error("REJECT %s: %s", d.source_path, "; ".join(f"{i.code}" for i in d.issues))
    for d in docs:
        for w in d.warnings:
            log.warning("WARN  %s: [%s] %s", d.source_path, w.code, w.message)

    stats = {
        "scanned": len(docs),
        "accepted": len(accepted),
        "rejected": len(rejected),
        "skipped_unchanged": 0,
        "ingested": 0,
        "chunks_written": 0,
        "duration_s": 0.0,
    }

    if dry_run:
        stats["duration_s"] = round(time.monotonic() - started, 2)
        return stats

    embedder: Embedder = (
        FakeEmbedder(settings.embedding_dim)
        if use_fake
        else BgeM3Embedder(settings.embedding_model, settings.embedding_dim, settings.embedding_batch_size, settings.embedding_device)
    )

    with get_conn() as conn:
        init_schema()

        # Fail-closed: vô hiệu hóa tài liệu lỗi đã từng được nạp
        for d in rejected:
            with conn.transaction():
                conn.execute(
                    """
                    UPDATE document_versions v SET approved = FALSE
                    FROM documents d
                    WHERE d.document_id = v.document_id AND d.document_code = %s
                    """,
                    (d.doc_id,),
                )

        # Lọc tài liệu cần nạp (bỏ qua nếu content_hash không đổi)
        existing_hashes = dict(conn.execute(
            """
            SELECT DISTINCT ON (d.document_code) d.document_code, v.content_hash
            FROM documents d
            JOIN document_versions v ON v.document_id = d.document_id
            ORDER BY d.document_code, v.updated_at DESC
            """
        ).fetchall())

        pending: list[ParsedDocument] = []
        for d in accepted:
            if not force and existing_hashes.get(d.doc_id) == d.content_hash:
                stats["skipped_unchanged"] += 1
            else:
                pending.append(d)

        if pending:
            texts = [embedding_text(d.meta, c) for d in pending for c in d.chunks]  # type: ignore[arg-type]
            log.info("Embedding %d chunk từ %d tài liệu ...", len(texts), len(pending))
            vectors = embedder.embed(texts)
            pos = 0
            for d in pending:
                n = len(d.chunks)
                _persist_document(conn, d, vectors[pos:pos + n])
                pos += n
                stats["ingested"] += 1
                stats["chunks_written"] += n
                log.info("OK    %s (%d chunk)", d.source_path, n)

            _persist_relations(conn, accepted)

    stats["duration_s"] = round(time.monotonic() - started, 2)
    return stats


# ════════════════════════════════════════════════════════════════════════════
# 9. CLI tối giản
# ════════════════════════════════════════════════════════════════════════════
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest tài liệu vào PostgreSQL pgvector")
    parser.add_argument("cmd", choices=["run"], default="run", nargs="?", help="Lệnh thực thi")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ kiểm tra và chunk, không ghi DB")
    parser.add_argument("--force", action="store_true", help="Nạp lại kể cả tài liệu không đổi")
    parser.add_argument("--fake", action="store_true", help="Dùng FakeEmbedder để kiểm thử nhanh")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = get_settings()

    stats = run_ingest(settings, use_fake=args.fake, dry_run=args.dry_run, force=args.force)
    log.info("Tổng kết: %s", json.dumps(stats, ensure_ascii=False))
    return 1 if stats["rejected"] > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
