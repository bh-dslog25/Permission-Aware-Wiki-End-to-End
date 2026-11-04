"""Step 4 - Constrained hybrid retrieval.

Retrieval against normalized document versions and chunks.

Module nay chi dung thu vien chuan; DB, embedder, reranker duoc inject qua Protocol.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol, Sequence

logger = logging.getLogger(__name__)


class RetrievalError(RuntimeError):
    """Loi ha tang khi truy xuat. Pipeline phai fail closed."""


# --------------------------------------------------------------------------- #
# Interfaces
# --------------------------------------------------------------------------- #
class Database(Protocol):
    def fetch_all(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Chay SQL voi named params (%(name)s) va tra ve list dict."""


class Embedder(Protocol):
    def embed_query(self, text: str) -> Sequence[float]: ...


class Reranker(Protocol):
    def rerank(self, query: str, candidates: list["Candidate"]) -> list["Candidate"]:
        """Tra ve cung tap candidate theo thu tu moi (tot nhat truoc)."""


@dataclass(frozen=True)
class UserScope:
    """Ket qua cua Policy Adapter (OpenFGA + ABAC) - nguon DUY NHAT cua ranh gioi quyen.

    allowed_unit_ids   : don vi ma user la viewer (da ke thua theo cay to chuc).
    max_classification : muc do mat toi da duoc phep (int 0-3, tu ABAC clearance).
    unit_chain         : chuoi don vi ap dung cho NGU CANH dang chon, tu rong -> hep.
    """

    user_id: str
    context_id: str
    allowed_unit_ids: tuple[str, ...]
    max_classification: int
    unit_chain: tuple[str, ...] = field(default_factory=tuple)
    unit_labels: dict[str, str] = field(default_factory=dict)
    context_label: str = ""


@dataclass(frozen=True)
class RetrievalConfig:
    per_branch_k: int = 40
    final_k: int = 8
    rerank_pool: int = 20
    rrf_k: int = 60
    fts_config: str = "simple"
    doc_statuses: tuple[str, ...] = ("active", "superseded", "expired")
    expand_related: bool = True
    expand_limit: int = 6


# --------------------------------------------------------------------------- #
# Data objects
# --------------------------------------------------------------------------- #
def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass
class Candidate:
    chunk_id: str
    document_id: str
    title: str
    content: str
    content_hash: str | None
    chunk_index: int
    owner_unit_id: str
    scope: str | None
    topic: str | None
    doc_type: str | None
    classification: int         # so nguyen 0-3
    version_number: str | None  # truong "version" cua documents
    valid_from: date | None
    valid_to: date | None
    doc_status: str             # status trong documents
    mandatory: bool
    # Quan he phien ban tu document_version_relations (co the None)
    superseded_by: str | None = None
    amended_by: str | None = None
    parent_conformance: str | None = None  # confirmed | unverified | conflicting
    policy_values: dict[str, Any] | None = None
    keyword_rank: int | None = None
    vector_rank: int | None = None
    score: float = 0.0
    via_expansion: bool = False

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "Candidate":
        return cls(
            chunk_id=str(row["chunk_id"]),
            document_id=str(row["document_id"]),
            title=row.get("title") or "",
            content=row["content"],
            content_hash=row.get("content_hash"),
            chunk_index=int(row.get("chunk_index") or 0),
            owner_unit_id=str(row.get("owner_unit") or row.get("owner_unit_id") or ""),
            scope=row.get("scope"),
            topic=row.get("topic"),
            doc_type=row.get("doc_type"),
            classification=int(row.get("classification") or 0),
            version_number=None if row.get("version") is None else str(row["version"]),
            valid_from=_to_date(row.get("valid_from")),
            valid_to=_to_date(row.get("valid_to")),
            doc_status=row.get("status") or "",
            mandatory=bool(row.get("mandatory")),
            superseded_by=row.get("superseded_by"),
            amended_by=row.get("amended_by"),
            parent_conformance=row.get("parent_conformance"),
            policy_values=row.get("policy_values"),
        )


@dataclass
class RetrievalResult:
    candidates: list[Candidate]
    keyword_hits: int = 0
    vector_hits: int = 0
    expanded: int = 0
    rejected_out_of_scope: int = 0  # phai luon = 0; >0 nghia la filter SQL co loi


# --------------------------------------------------------------------------- #
# SQL - moi tham so di qua named params; khong noi chuoi du lieu nguoi dung.
# Normalized schema: documents, document_versions, document_version_relations, chunks.
# --------------------------------------------------------------------------- #
_SELECT = """
SELECT
    c.chunk_id,
    d.document_code AS document_id,
    c.chunk_index,
    c.content,
    v.valid_from::date AS valid_from,
    v.valid_to::date AS valid_to,
    CASE v.status WHEN 'published' THEN 'active' ELSE v.status END AS status,
    v.mandatory,
    owner.unit_code AS owner_unit,
    v.classification,
    v.title,
    v.scope,
    v.topic,
    v.doc_type,
    v.version_label AS version,
    v.content_hash,
    v.parent_conformance,
    v.policy_values,
    sup_doc.document_code AS superseded_by,
    amd_doc.document_code AS amended_by
    {extra_cols}
FROM chunks c
JOIN document_versions v ON v.version_id = c.version_id
JOIN documents d ON d.document_id = v.document_id
JOIN organization_units owner ON owner.unit_id = v.owner_unit_id
LEFT JOIN document_version_relations sup
    ON sup.target_version_id = v.version_id AND sup.relation_type = 'supersedes'
LEFT JOIN document_versions sup_v ON sup_v.version_id = sup.source_version_id
LEFT JOIN documents sup_doc ON sup_doc.document_id = sup_v.document_id
LEFT JOIN document_version_relations amd
    ON amd.target_version_id = v.version_id AND amd.relation_type = 'amends'
LEFT JOIN document_versions amd_v ON amd_v.version_id = amd.source_version_id
LEFT JOIN documents amd_doc ON amd_doc.document_id = amd_v.document_id
"""

# Ranh gioi quyen + hieu luc.
# Dung chunk columns (denormalized) de filter truc tiep tai index.
# NULL o valid_from lam dieu kien khong thoa -> loai (default-deny).
_AUTHZ_WHERE = """
WHERE owner.unit_code = ANY(%(units)s)
  AND c.is_active
  AND v.classification <= %(max_class)s
  AND v.approved
  AND v.status = ANY(%(doc_statuses)s)
  AND v.valid_from::date <= %(as_of)s
  AND (v.valid_to IS NULL OR v.valid_to::date >= %(as_of)s)
"""

# Luu y hieu nang: voi HNSW + filter, pgvector >= 0.8 nen bat `hnsw.iterative_scan`.
# Cot content_tsv + GIN index trong chunks (tsvector).
_VECTOR_SQL = (
    _SELECT.format(extra_cols="")
    + _AUTHZ_WHERE
    + " ORDER BY c.embedding <=> %(qvec)s::vector LIMIT %(k)s"
)

_KEYWORD_SQL = (
    _SELECT.format(
        extra_cols=", ts_rank_cd(c.content_tsv, to_tsquery(%(fts)s::regconfig, %(tsq)s)) AS kw_rank"
    )
    + _AUTHZ_WHERE
    + " AND c.content_tsv @@ to_tsquery(%(fts)s::regconfig, %(tsq)s)"
    + " ORDER BY kw_rank DESC LIMIT %(k)s"
)

# Mo rong theo supersedes/amends qua document_version_relations.
_EXPAND_SQL = (
    _SELECT.format(extra_cols="")
    + _AUTHZ_WHERE
    + """
  AND c.chunk_id::text <> ALL(%(known_chunk_ids)s)
  AND (
        v.document_id IN (
            SELECT related.document_id
            FROM document_version_relations dr
            JOIN document_versions related ON related.version_id = dr.source_version_id
            JOIN documents related_doc ON related_doc.document_id = related.document_id
            WHERE related_doc.document_code = ANY(%(doc_ids)s)
              AND dr.relation_type IN ('supersedes', 'amends')
        )
        OR v.document_id IN (
            SELECT related.document_id
            FROM document_version_relations dr
            JOIN document_versions related ON related.version_id = dr.target_version_id
            JOIN documents related_doc ON related_doc.document_id = related.document_id
            WHERE related_doc.document_code = ANY(%(doc_ids)s)
              AND dr.relation_type IN ('supersedes', 'amends')
        )
  )
ORDER BY c.embedding <=> %(qvec)s::vector
LIMIT %(k)s
"""
)

# Phat hien ban thay the/bo sung ma user KHONG duoc xem.
# Chi tra ve document_id cua bang chung - khong tra noi dung cua ban an.
_HIDDEN_SUCCESSOR_SQL = """
SELECT DISTINCT target_doc.document_code AS document_id
FROM document_version_relations dr
JOIN document_versions target_v ON target_v.version_id = dr.target_version_id
JOIN documents target_doc ON target_doc.document_id = target_v.document_id
JOIN document_versions successor_v ON successor_v.version_id = dr.source_version_id
JOIN documents successor ON successor.document_id = successor_v.document_id
JOIN organization_units successor_owner ON successor_owner.unit_id = successor_v.owner_unit_id
JOIN chunks c_succ ON c_succ.version_id = successor_v.version_id
WHERE target_doc.document_code = ANY(%(doc_ids)s)
  AND dr.relation_type IN ('supersedes', 'amends')
  AND successor_v.status = ANY(%(doc_statuses)s)
  AND successor_v.approved
  AND c_succ.is_active
  AND successor_v.valid_from::date <= %(as_of)s
  AND (successor_v.valid_to IS NULL OR successor_v.valid_to::date >= %(as_of)s)
  AND (
        successor_owner.unit_code = ANY(%(units)s)
        AND successor_v.classification <= %(max_class)s
  ) IS NOT TRUE
"""


# --------------------------------------------------------------------------- #
# Helpers thuan (de test)
# --------------------------------------------------------------------------- #
_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_STOPWORDS = frozenset(
    "la cua va cac nhung mot co khong cho duoc trong khi voi toi minh ban gi nao "
    "the sao nhu ve theo nay do o tai tu den hay hoac thi ma de can phai xin hoi "
    "quy dinh chinh sach".split()
)


def _strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d").replace("Đ", "D")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(_strip_accents(text))]


def build_tsquery(keywords: Sequence[str], fallback_text: str = "") -> str:
    """Tao bieu thuc to_tsquery dang OR; cum nhieu tu duoc them dang phrase (a <-> b).

    Token chi gom chu/so nen an toan khi dua vao to_tsquery.
    """
    parts: list[str] = []
    for kw in keywords:
        toks = [t for t in tokenize(kw) if len(t) > 1 or t.isdigit()]
        if not toks:
            continue
        if len(toks) > 1:
            parts.append("(" + " <-> ".join(toks) + ")")
        parts.extend(toks)
    if not parts:
        parts = [t for t in tokenize(fallback_text) if t not in _STOPWORDS and (len(t) > 1 or t.isdigit())]
    seen: set[str] = set()
    uniq = [p for p in parts if not (p in seen or seen.add(p))]  # type: ignore[func-returns-value]
    return " | ".join(uniq)


def reciprocal_rank_fusion(rankings: Sequence[Sequence[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for pos, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + pos)
    return scores


def _vector_literal(vec: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.6f}" for x in vec) + "]"


# --------------------------------------------------------------------------- #
# Retriever
# --------------------------------------------------------------------------- #
class HybridRetriever:
    def __init__(
        self,
        db: Database,
        embedder: Embedder,
        reranker: Reranker | None = None,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._db = db
        self._embedder = embedder
        self._reranker = reranker
        self._cfg = config or RetrievalConfig()

    # ---- params ----------------------------------------------------------- #
    def _authz_params(self, scope: UserScope, as_of: date) -> dict[str, Any]:
        return {
            "units": list(scope.allowed_unit_ids),
            "max_class": scope.max_classification,
            "doc_statuses": [
                "published" if status == "active" else status
                for status in self._cfg.doc_statuses
            ],
            "as_of": as_of,
        }

    # ---- main ------------------------------------------------------------- #
    def search(
        self,
        *,
        question: str,
        keywords: Sequence[str],
        scope: UserScope,
        as_of: date,
        historical: bool = False,
    ) -> RetrievalResult:
        cfg = self._cfg
        # Fail closed: khong co quyen xac dinh -> khong truy xuat gi.
        if not scope.allowed_unit_ids:
            return RetrievalResult([])

        params = self._authz_params(scope, as_of)
        try:
            qvec = _vector_literal(self._embedder.embed_query(question))
            vector_rows = self._db.fetch_all(_VECTOR_SQL, {**params, "qvec": qvec, "k": cfg.per_branch_k})
            tsq = build_tsquery(keywords, question)
            keyword_rows = (
                self._db.fetch_all(
                    _KEYWORD_SQL,
                    {**params, "fts": cfg.fts_config, "tsq": tsq, "k": cfg.per_branch_k},
                )
                if tsq
                else []
            )
        except Exception as exc:  # noqa: BLE001
            raise RetrievalError("retrieval backend failed") from exc

        by_id: dict[str, Candidate] = {}
        rejected = 0

        def _admit(row: dict[str, Any]) -> Candidate | None:
            nonlocal rejected
            cand = Candidate.from_row(row)
            if not self._in_scope(cand, scope):
                rejected += 1
                logger.error("Chunk %s thoat khoi filter quyen - bo va bao loi", cand.chunk_id)
                return None
            return by_id.setdefault(cand.chunk_id, cand)

        kw_ids: list[str] = []
        for rank, row in enumerate(keyword_rows, start=1):
            cand = _admit(row)
            if cand:
                cand.keyword_rank = rank
                kw_ids.append(cand.chunk_id)
        vec_ids: list[str] = []
        for rank, row in enumerate(vector_rows, start=1):
            cand = _admit(row)
            if cand:
                cand.vector_rank = rank
                vec_ids.append(cand.chunk_id)

        scores = reciprocal_rank_fusion([kw_ids, vec_ids], cfg.rrf_k)
        for cid, cand in by_id.items():
            cand.score = scores.get(cid, 0.0)
        ordered = sorted(by_id.values(), key=lambda c: (-c.score, c.chunk_id))

        top = self._maybe_rerank(question, ordered)[: cfg.final_k]

        expanded: list[Candidate] = []
        if cfg.expand_related and top:
            expanded = self._expand(top, qvec, params, scope)

        return RetrievalResult(
            candidates=top + expanded,
            keyword_hits=len(kw_ids),
            vector_hits=len(vec_ids),
            expanded=len(expanded),
            rejected_out_of_scope=rejected,
        )

    # ---- internals -------------------------------------------------------- #
    @staticmethod
    def _in_scope(cand: Candidate, scope: UserScope) -> bool:
        """Lop phong thu thu hai; filter SQL moi la ranh gioi chinh."""
        return (
            cand.owner_unit_id in scope.allowed_unit_ids
            and cand.classification <= scope.max_classification
        )

    def _maybe_rerank(self, question: str, ordered: list[Candidate]) -> list[Candidate]:
        if not self._reranker or not ordered:
            return ordered
        pool, rest = ordered[: self._cfg.rerank_pool], ordered[self._cfg.rerank_pool :]
        try:
            reranked = self._reranker.rerank(question, pool)
            if {c.chunk_id for c in reranked} != {c.chunk_id for c in pool}:
                raise ValueError("reranker tra ve tap chunk khac")
            return reranked + rest
        except Exception:  # noqa: BLE001 - reranker la tuy chon, roi ve thu hang RRF
            logger.warning("Reranker loi, dung thu hang RRF", exc_info=True)
            return ordered

    def _expand(
        self, top: list[Candidate], qvec: str, params: dict[str, Any], scope: UserScope
    ) -> list[Candidate]:
        known_chunk_ids = sorted({c.chunk_id for c in top})
        doc_ids = sorted({c.document_id for c in top})
        try:
            rows = self._db.fetch_all(
                _EXPAND_SQL,
                {**params, "known_chunk_ids": known_chunk_ids, "doc_ids": doc_ids, "qvec": qvec, "k": self._cfg.expand_limit},
            )
        except Exception as exc:  # noqa: BLE001
            raise RetrievalError("expansion failed") from exc
        out: list[Candidate] = []
        known = {c.chunk_id for c in top}
        for row in rows:
            cand = Candidate.from_row(row)
            if cand.chunk_id in known or not self._in_scope(cand, scope):
                continue
            cand.via_expansion = True
            out.append(cand)
        return out

    def find_hidden_successors(
        self, document_ids: Sequence[str], scope: UserScope, as_of: date
    ) -> set[str]:
        """Tra ve cac document_id (da cap quyen) ma co ban thay the/bo sung hieu luc nhung user khong xem duoc."""
        if not document_ids:
            return set()
        params = {
            "doc_ids": sorted(set(document_ids)),
            "units": list(scope.allowed_unit_ids),
            "max_class": scope.max_classification,
            "doc_statuses": list(self._cfg.doc_statuses),
            "as_of": as_of,
        }
        try:
            rows = self._db.fetch_all(_HIDDEN_SUCCESSOR_SQL, params)
        except Exception as exc:  # noqa: BLE001
            raise RetrievalError("successor check failed") from exc
        return {str(r["document_id"]) for r in rows}
