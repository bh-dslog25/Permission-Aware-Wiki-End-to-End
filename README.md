# Permission-Aware RAG Wiki

Wiki nội bộ doanh nghiệp dạng Retrieval-Augmented Generation (RAG), trong đó câu trả lời không chỉ phụ thuộc vào chất lượng tìm kiếm và sinh văn bản, mà còn bị ràng buộc chặt chẽ bởi **quyền truy cập theo tổ chức, mức độ phân loại dữ liệu, phạm vi tài liệu và thời điểm hiệu lực**.

Nguyên tắc cốt lõi: *"người dùng chỉ được xem dữ liệu mà họ có quyền thực sự"*. Hệ thống **fail closed** và **default deny**: thiếu metadata quyền, thiếu context, không rõ hiệu lực hoặc OpenFGA không xác định được quyền thì hệ thống từ chối, không trả lời đoán.

## 1. Project Overview

### 1.1. Problem Statement

Trong Wiki nội bộ, không phải nhân viên nào cũng được phép truy cập mọi tài liệu. Một câu trả lời có thể sai hoặc gây rủi ro nếu hệ thống:

- Truy xuất tài liệu nằm ngoài phạm vi quyền của người dùng.
- Sử dụng chính sách đã hết hiệu lực, đã bị thay thế hoặc còn là bản nháp/chưa phê duyệt.
- Không xử lý đúng quan hệ giữa chính sách cấp tập đoàn, công ty, phòng ban và nhóm.
- Tạo câu trả lời không có bằng chứng hoặc trích dẫn không chính xác.
- Để LLM tự quyết định người dùng có quyền đọc tài liệu hay không.

### 1.2. Project Objectives

1. Quản lý tài liệu chính sách, quy định, quy trình, FAQ, hướng dẫn và báo cáo nội bộ dưới dạng RAG.
2. Kiểm soát quyền truy cập **trước** khi tài liệu được đưa vào kết quả retrieval, ở cả tầng dữ liệu lẫn tầng truy xuất.
3. Truy xuất kết hợp từ khóa và vector để tăng khả năng tìm kiếm.
4. Xác định đúng phiên bản tài liệu có hiệu lực tại thời điểm được hỏi.
5. Trả lời dựa trên bằng chứng được phép truy cập, kèm citation.
6. Từ chối hoặc yêu cầu làm rõ khi không đủ bằng chứng hay không thể xác định chính sách áp dụng.
7. Hỗ trợ demo nhiều context/role (kể cả người kiêm nhiệm) trong cùng một hệ thống.
8. Ghi nhận quyết định phân quyền và nguồn được sử dụng để phục vụ audit.

### 1.3. MVP Scope

- 30–50 tài liệu Markdown giả lập (có bản cũ/mới/hết hạn/nháp, tài liệu mật, chính sách nhiều cấp).
- Tài khoản, cây tổ chức, membership và role được lưu trong PostgreSQL; `data/org.yaml` không còn là nguồn runtime.
- Tài liệu có phạm vi, phân loại và thời gian hiệu lực khác nhau.
- OpenFGA để kiểm tra quan hệ truy cập (ReBAC); ABAC cho phân loại, hiệu lực và trạng thái.
- PostgreSQL + pgvector để lưu metadata, chunks, vector embeddings và audit log.
- Hybrid retrieval: keyword/BM25 + vector search, hợp nhất bằng Reciprocal Rank Fusion (RRF), reranker tùy chọn.
- LLM tạo câu trả lời có citation hoặc từ chối.
- Bộ câu hỏi kiểm thử phân quyền, phiên bản tài liệu và chất lượng trả lời.

MVP sử dụng danh tính demo để kiểm thử. Đây không phải cơ chế xác thực phù hợp cho production.

---

## 2. Technology Stack

| Thành phần | Công nghệ | Trách nhiệm |
|---|---|---|
| Backend | Python, FastAPI | API và điều phối xử lý |
| Authorization | OpenFGA | Kiểm tra quan hệ truy cập (ReBAC) |
| Policy Adapter | Python | Kết hợp kết quả quan hệ với điều kiện ABAC, tạo bộ lọc truy vấn (`allowed_units`, `max_classification`, điều kiện hiệu lực) |
| Database | PostgreSQL | Lưu tài liệu, phiên bản, metadata, chunks và audit log |
| Vector Search | pgvector | Tìm kiếm ngữ nghĩa |
| Keyword Search | PostgreSQL full-text search (`content_tsv`) với cấu hình phù hợp hoặc BM25 | Tìm kiếm theo từ khóa, mã chính sách |
| Embedding | BGE-M3 | Chuyển chunks và câu hỏi thành vector |
| Ranking | RRF; reranker là tùy chọn | Hợp nhất và cải thiện thứ hạng kết quả đã qua lọc quyền |
| LLM | Gemini API hoặc mô hình local phù hợp | Sinh câu trả lời dựa trên bằng chứng |
| Frontend | React + TypeScript | Chat, chọn persona demo, hiển thị nguồn |
| Deployment | Docker Compose | Khởi chạy PostgreSQL, OpenFGA và các dịch vụ MVP |

Lưu ý: OpenFGA không tự sinh các điều kiện SQL như `allowed_units`. Policy Adapter (`policy_adapter.py`) chuyển kết quả kiểm tra quan hệ và chính sách ABAC thành bộ lọc thực thi được.

---

## 3. Repository Structure

Cấu trúc dưới đây kết hợp bố cục ban đầu với các thành phần bổ sung từ bản triển khai mới (đánh dấu `[mới]`).

```text
permission-aware-rag-wiki/
├── README.md
├── docker-compose.yml            # PostgreSQL + pgvector, OpenFGA (chạy local)
├── .env.example
│
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py               # Khởi tạo FastAPI, đăng ký routers
│   │   ├── config.py             # Cấu hình tập trung, đọc từ .env ở thư mục gốc
│   │   ├── db.py                 # Kết nối và thao tác DB dùng chung
│   │   ├── auth.py               # Danh tính + context đang hoạt động
│   │   ├── llm.py                # Client LLM (Gemini / local)
│   │   ├── audit.py              # Logic ghi/truy vấn audit dùng chung
│   │   │
│   │   ├── api/
│   │   │   ├── query.py          # POST /ask, GET /me
│   │   │   ├── feedback.py
│   │   │   ├── audit.py          # Endpoint HTTP truy vấn audit
│   │   │   └── evaluate.py       # Tùy chọn; ưu tiên chạy eval bằng CLI
│   │   │
│   │   ├── authz/
│   │   │   ├── model.fga         # Authorization model của OpenFGA
│   │   │   ├── seed.py           # Đồng bộ tuples từ PostgreSQL sang OpenFGA
│   │   │   └── policy_adapter.py # OpenFGA + ABAC -> allowed_units, max_classification
│   │   │
│   │   ├── ingest/
│   │   │   └── ingest.py         # Parse, validate metadata, chunk, embed, index
│   │   │
│   │   └── rag/
│   │       ├── pipeline.py       # Điều phối các bước RAG
│   │       ├── query_understanding.py  # [mới] Câu hỏi -> structured query
│   │       ├── retrieval.py      # Hybrid search có ràng buộc + RRF
│   │       ├── rerank.py         # [mới] Reranker tùy chọn
│   │       ├── resolution.py     # Hiệu lực, supersedes/amends, mandatory
│   │       └── generation.py     # Sinh câu trả lời, verify citation, abstain
│   │
│   ├── db/
│   │   └── schema.sql            # Schema tập trung (documents, versions, chunks, relations, audit_log)
│   │
│   ├── scripts/                  # [mới] CLI cho vận hành và đánh giá
│   │   ├── init_db.py            # Áp dụng schema.sql
│   │   ├── run_eval.py           # Chạy golden dataset, xuất metrics
│   │   └── run_leakage_check.py  # Kiểm tra chunk trái phép tại ranh giới retrieval
│   │
│   └── tests/
│       ├── test_authz.py
│       ├── test_leakage.py
│       ├── test_temporal.py
│       ├── test_resolution.py
│       └── test_audit.py         # [mới] Kiểm tra audit_log append-only
│
├── frontend/
│   ├── package.json
│   └── src/
│       ├── api.ts
│       ├── App.tsx
│       └── components/
│           ├── ChatPage.tsx
│           ├── PersonaSwitcher.tsx
│           ├── ContextBadge.tsx  # "Đang trả lời theo: Công ty › Phòng, bản hiệu lực ngày Z"
│           └── SourceCard.tsx
│
├── data/
│   ├── org.yaml                  # Dữ liệu demo cũ; không được đọc lúc chạy
│   ├── documents/                # Tài liệu Markdown giả lập (front matter + nội dung)
│   └── eval/
│       └── golden.jsonl
│
└── docs/
    └── architecture.md
```

### 3.1. Quy ước tổ chức code

- `main.py`: khởi tạo FastAPI và đăng ký routers.
- `config.py`: cấu hình tập trung; tên biến phải thống nhất với `.env.example` và Docker Compose.
- `api/query.py`: gộp `POST /ask` và `GET /me`.
- `api/feedback.py`: tiếp nhận phản hồi về câu trả lời.
- `api/audit.py`: endpoint HTTP truy vấn audit log theo quyền người gọi.
- `api/evaluate.py`: giao diện chạy đánh giá nếu cần; trong MVP ưu tiên CLI ở `scripts/`.
- `audit.py`: logic ghi nhận và truy vấn audit dùng chung, không trùng trách nhiệm với `api/audit.py`.
- `auth.py`: giải quyết danh tính và context đang hoạt động.
- `db.py`: kết nối database và thao tác DB dùng chung.
- `authz/model.fga`: authorization model của OpenFGA.
- `authz/seed.py`: đồng bộ cây đơn vị và memberships đang hiệu lực từ PostgreSQL sang OpenFGA.
- `authz/policy_adapter.py`: chuyển thông tin quyền thành điều kiện truy vấn an toàn; fail closed khi lỗi.
- `ingest/ingest.py`: gộp parsing, validate metadata, chunking, embedding và indexing ở quy mô MVP.
- `rag/pipeline.py`: điều phối các bước RAG.
- `rag/query_understanding.py`: phân tích câu hỏi thành structured query (không sinh quyết định quyền).
- `rag/retrieval.py`: tìm kiếm kết hợp có ràng buộc quyền và hợp nhất bằng RRF.
- `rag/rerank.py`: sắp xếp lại các kết quả **đã qua lọc quyền**; tùy chọn.
- `rag/resolution.py`: xác định phiên bản và quy tắc áp dụng.
- `rag/generation.py`: tạo câu trả lời, kiểm tra citation và xử lý từ chối.
- `db/schema.sql`: schema tập trung, tránh tạo bảng rải rác trong code.
- `data/org.yaml`: mẫu dữ liệu demo cũ, không phải nguồn dữ liệu runtime.

Không cần tạo file riêng cho từng hàm. Khi một module lớn lên hoặc cần kiểm thử, tái sử dụng độc lập, có thể tách thêm sau.

---

## 4. System Architecture

Hệ thống gồm 3 lớp chính:

1. **Dữ liệu và vector store:** PostgreSQL + pgvector lưu tài liệu, phiên bản, chunks, embedding, metadata phân quyền và audit log.
2. **Quyền và xác thực:** PostgreSQL lưu tài khoản, cây đơn vị, memberships và role assignments; OpenFGA đánh giá quan hệ ReBAC; Policy Adapter áp dụng clearance, trạng thái và hiệu lực tài liệu theo dữ liệu DB.
3. **Truy xuất và sinh câu trả lời:** tìm kiếm từ khóa + vector, ràng buộc quyền ngay trong SQL; tài liệu không đủ điều kiện bị loại trước khi đến LLM; kết quả và quyết định truy cập được ghi vào audit log.

### 4.1. Offline Ingestion Pipeline

```text
Markdown Documents
        |
        v
Parse Front Matter + Content
        |
        v
Validate Required Metadata
        |
        v
Chunk by Article / Clause / Section
        |
        v
Generate Embeddings (BGE-M3)
        |
        v
Store Metadata + Chunks + Vectors
        |
        v
PostgreSQL + pgvector
```

Các bước chính:

1. Đọc nội dung và metadata từ tài liệu.
2. Kiểm tra trường bắt buộc: `owner_unit`, `classification`, `status`, `approved`, `valid_from`/`valid_to`.
3. Chia tài liệu theo Điều/Khoản/Mục để giữ ngữ cảnh và trích dẫn chính xác.
4. Sinh embedding bằng BGE-M3 và `content_tsv` cho keyword search.
5. Lưu chunks cùng metadata lọc (`owner_unit`, `classification`, hiệu lực, trạng thái, phê duyệt) và vector vào PostgreSQL.
6. Tài liệu thiếu metadata bắt buộc vào trạng thái quarantine hoặc bị từ chối index cho retrieval thông thường (default deny).

Nếu tài liệu có metadata sai, không được tự suy đoán quyền truy cập từ nội dung văn bản. Việc bổ sung hoặc sửa metadata phải qua quy trình kiểm duyệt của Knowledge Owner.

### 4.2. Online Query Pipeline

```text
User Question
      |
      v
Resolve Trusted Identity + Context
      |
      v
Parse Question into Structured Query
      |
      v
OpenFGA + Policy Adapter
      |
      v
Authorized SQL / Search Filters
      |
      v
Filtered Keyword Search + Vector Search
      |
      v
RRF + Optional Reranking
      |
      v
Temporal / Policy Resolution
      |
      v
Generate Answer with Citations
      |
      v
Citation Verification + Audit
      |
      v
Answer or Abstain
```

#### Step 1 — Identity and Context

Backend xác định danh tính từ nguồn đáng tin cậy và kiểm tra context người dùng được phép dùng. Người kiêm nhiệm chọn **một** context mỗi lần; quyền không được gộp giữa các context.

`PersonaSwitcher` chỉ liệt kê tài khoản active lấy từ database. Client không được tự khai báo `allowed_units`, quyền đọc hoặc cấp phân loại.

#### Step 2 — Query Understanding

Chuyển câu hỏi tự nhiên thành structured query cố định: `intent`, `topic`, `subject`, `time`, `requested_scope`, `keywords`. Đầu ra chỉ mô tả ý định và phạm vi, không chứa quyết định phân quyền có thẩm quyền.

#### Step 3 — Authorization

- **ReBAC (OpenFGA):** kiểm tra user có được gán (`assigned`) vào context đơn vị hay không, rồi liệt kê các đơn vị user được xem theo kế thừa trong cây tổ chức, tạo `allowed_units`.
- **ABAC (Policy Adapter):** kết hợp classification/clearance (`max_classification`), thời điểm hỏi, trạng thái và phê duyệt tài liệu để tạo điều kiện truy vấn.

Nếu không xác định được quyền, thiếu metadata bắt buộc hoặc OpenFGA lỗi/timeout, hệ thống **fail closed**: không trả về bằng chứng chưa được xác minh quyền.

#### Step 4 — Constrained Hybrid Retrieval

Keyword search và vector search chạy trên tập đã áp dụng bộ lọc quyền, hiệu lực và phạm vi, ngay trong SQL.

- Keyword search: thuật ngữ, mã chính sách, cụm từ cụ thể.
- Vector search: nội dung gần về ngữ nghĩa.
- RRF: hợp nhất thứ hạng hai nhánh.
- Reranker (tùy chọn): sắp xếp lại các kết quả đã qua lọc quyền.

Không truy xuất toàn bộ tài liệu rồi mới loại kết quả trái phép sau ranking. Cả hai nhánh phải tuân thủ cùng ranh giới quyền.

#### Step 5 — Temporal and Policy Resolution

- Loại bỏ phiên bản không phù hợp với thời điểm truy vấn; phân biệt truy vấn hiện hành và lịch sử (`as_of`).
- Xử lý quan hệ `supersedes` và `amends`.
- Áp dụng quy tắc bắt buộc giữa chính sách cấp cha và cấp con theo quy tắc nghiệp vụ đã định nghĩa (chính sách cấp trên `mandatory` là sàn/trần).
- Từ chối kết luận nếu có xung đột chưa giải quyết, thiếu bằng chứng, hoặc bản thay thế mà người dùng không được xem.

Không mặc định chính sách cấp con luôn thắng chính sách cấp cha.

#### Step 6 — Generation and Verification

LLM chỉ nhận bằng chứng đã được kiểm soát quyền và chọn lọc theo thời gian; không có cơ chế bypass quyền qua prompt.

Đầu ra gồm: câu trả lời, phạm vi áp dụng, thời điểm hiệu lực, citation tới nguồn được phép truy cập, hoặc trạng thái từ chối/làm rõ. Kiểm tra citation xác nhận nguồn trích dẫn thực sự nằm trong tập bằng chứng được cung cấp; việc này không thay thế kiểm soát quyền tại retrieval.

Khi bị chặn hoặc không có tài liệu, hệ thống trả cùng một thông điệp chung, không tiết lộ sự tồn tại của tài liệu bị ẩn.

---

## 5. Authorization Model

### 5.1. Cây tổ chức và kế thừa

Tổ chức được mô hình hóa: tập đoàn → công ty → phòng ban → nhóm. Cây đơn vị được lưu trong `organization_units`:

```text
A Group
├── Company A
│   └── Sales
│       └── Sales Team 1
└── Company B
    └── Tech
        └── Backend Team
```

Mỗi user thuộc một hoặc nhiều team. Khi user chọn một context, hệ thống chỉ tính quyền trong context đó.

### 5.2. Lớp 1 — ReBAC với OpenFGA

OpenFGA quản lý quan hệ như user `assigned`/`member_of` đơn vị, team `part_of` department, department `part_of` company, tài liệu `owned_by` đơn vị, và kế thừa theo cây tổ chức. Mục tiêu trả lời:

- User có được gán vào context đơn vị này không?
- Trong context đó, user được xem những đơn vị nào?

### 5.3. Lớp 2 — ABAC với classification và clearance

Các mức phân loại: `public` < `internal` < `confidential` < `restricted`. Clearance tối đa của role được lưu trong `roles.max_classification`.

| Role | Clearance tối đa |
|---|---|
| `employee` | `internal` |
| `manager` | `confidential` |
| `hr_owner` | `confidential` |
| `security` | `restricted` |

Chỉ chunk có classification ≤ `max_classification` của user mới được phép xuất hiện.

### 5.4. Quy tắc xem dữ liệu

Một chunk chỉ được truy xuất khi **tất cả** điều kiện sau đúng:

1. User được gán vào context hiện tại.
2. Tài liệu thuộc đơn vị trong `allowed_units` (đơn vị của context hoặc đơn vị cha theo kế thừa).
3. Classification không vượt clearance của user.
4. `approved = true`.
5. `status` không phải `draft`.
6. `valid_from <= as_of <= valid_to` (`valid_to` null nghĩa là chưa hết hạn).

Ngoại lệ có chủ đích: truy vấn lịch sử (`as_of` trong quá khứ) vẫn có thể lấy bản `superseded`/`expired` từng hiệu lực tại thời điểm đó, nhưng vẫn qua đủ các bộ lọc quyền.

### 5.5. Default deny

Dữ liệu bị chặn hoàn toàn nếu: thiếu metadata, không xác định được đơn vị sở hữu, OpenFGA không trả về quyền, không rõ hiệu lực, hoặc classification quá cao.

---

## 6. Database Design

PostgreSQL là nguồn lưu trữ chính. Quan hệ phân quyền được quản lý trong OpenFGA; không sao chép toàn bộ tuples vào bảng quyền tự thiết kế để ra quyết định truy cập. Schema đầy đủ nằm ở `backend/db/schema.sql`.

### 6.1. Core Tables

#### `users`

| Field | Purpose |
|---|---|
| `user_id` | Khóa định danh người dùng |
| `display_name` | Tên hiển thị |
| `identity_source` | Nguồn danh tính hoặc loại tài khoản demo |
| `status` | Trạng thái tài khoản |

#### `org_units`

| Field | Purpose |
|---|---|
| `unit_id` | Định danh đơn vị |
| `unit_name` | Tên đơn vị |
| `unit_type` | Group, company, department hoặc team |
| `parent_unit_id` | Đơn vị cấp cha, nếu có |

#### `memberships`

| Field | Purpose |
|---|---|
| `membership_id` | Khóa định danh |
| `user_id` | Người dùng |
| `unit_id` | Đơn vị tham gia |
| `role` | Vai trò (quyết định clearance) |
| `valid_from` / `valid_to` | Khoảng hiệu lực của membership |

Dữ liệu membership được đọc từ PostgreSQL và đồng bộ vào OpenFGA. Không dùng riêng bảng này để bỏ qua quyết định của OpenFGA.

#### `documents`

| Field | Purpose |
|---|---|
| `document_id` | Định danh tài liệu logic |
| `title` | Tiêu đề |
| `owner_unit_id` | Đơn vị sở hữu |
| `scope` | Phạm vi nghiệp vụ |
| `topic` | Chủ đề (ví dụ `leave_policy`) |
| `doc_type` | Policy, Process, Guideline, FAQ... |
| `classification` | Cấp phân loại |
| `status` | Trạng thái tài liệu |

#### `document_versions`

| Field | Purpose |
|---|---|
| `version_id` | Định danh phiên bản |
| `document_id` | Tài liệu logic |
| `version_number` | Số phiên bản |
| `valid_from` / `valid_to` | Khoảng hiệu lực (`valid_to` null = chưa hết hạn) |
| `status` | `draft`, `active`, `superseded`, `expired` |
| `approved` | Đã được Knowledge Owner phê duyệt chưa |
| `mandatory` | Có phải quy tắc bắt buộc theo mô hình nghiệp vụ không |
| `supersedes_version_id` | Phiên bản bị thay thế, nếu có |
| `content_hash` | Hash kiểm tra toàn vẹn nội dung |

`status`, `approved` và các mốc thời gian phải được định nghĩa nhất quán. Draft hoặc chưa phê duyệt không được xuất hiện trong retrieval thông thường.

#### `document_relations`

| Field | Purpose |
|---|---|
| `relation_id` | Khóa định danh |
| `from_version_id` | Phiên bản nguồn |
| `to_version_id` | Phiên bản đích |
| `relation_type` | `supersedes` hoặc `amends` |

Mở rộng theo quan hệ này phải đi qua cùng bộ lọc quyền.

#### `chunks`

| Field | Purpose |
|---|---|
| `chunk_id` | Định danh chunk |
| `document_id` / `version_id` | Tài liệu và phiên bản nguồn |
| `chunk_index` | Vị trí chunk |
| `content` | Nội dung chunk |
| `embedding` | Vector embedding (pgvector) |
| `content_tsv` | Cột full-text search |
| `owner_unit` | Đơn vị sở hữu (thừa hưởng từ tài liệu) |
| `classification` | Cấp phân loại |
| `valid_from` / `valid_to` | Hiệu lực |
| `status` / `approved` | Trạng thái và phê duyệt |
| `content_hash` | Hash nội dung chunk |
| `metadata` | Metadata bổ sung đã xác thực |

Chunk là đơn vị truy xuất chính. Các trường quyền và thời gian được lưu thành cột riêng (không chỉ trong JSON) để lọc và lập chỉ mục hiệu quả; chúng chỉ mang thuộc tính của tài liệu, **không** mang danh sách quyền theo từng user. Quyền của user được tính lúc truy vấn nên đổi tổ chức không cần reindex; chỉ reindex khi tài liệu đổi chủ sở hữu, độ mật hoặc trạng thái.

#### `audit_log`

Append-only: không cho sửa hoặc xóa.

| Field | Purpose |
|---|---|
| `audit_id` | Định danh bản ghi |
| `request_id` | ID của request |
| `actor_id` | Người thực hiện |
| `active_context` | Context được sử dụng |
| `action` | Hành động |
| `authorization_result` | Kết quả quyết định quyền (`allowed_units`, `max_classification`, allow/deny) |
| `source_ids` | ID nguồn được sử dụng hoặc trả về |
| `source_hashes` | Hash nguồn để truy vết phiên bản |
| `created_at` | Thời điểm ghi log |

Không ghi nội dung bí mật, token hoặc dữ liệu nhạy cảm không cần thiết. Việc lưu câu hỏi/câu trả lời phải theo chính sách lưu trữ dữ liệu.

---

## 7. Input and Output Contracts

### 7.1. Ask API

`POST /api/v1/ask`

```json
{
  "question": "Chính sách nghỉ phép năm hiện hành là gì?",
  "context_id": "ctx_risk",
  "as_of": null
}
```

- `question`: câu hỏi của người dùng.
- `context_id`: context muốn dùng, phải được backend xác minh.
- `as_of`: thời điểm tra cứu; `null` là thời điểm hiện tại theo quy ước hệ thống.

Danh tính không lấy từ trường `user_id` tùy ý do client gửi; backend xác định từ authentication middleware hoặc ánh xạ tài khoản demo.

### 7.2. Structured Query

```json
{
  "intent": "policy_lookup",
  "topic": "leave_policy",
  "subject": "annual_leave",
  "time": { "mode": "current", "at": null },
  "requested_scope": "my_applicable_policy",
  "keywords": ["nghỉ phép", "phép năm"]
}
```

Đây chỉ là cấu trúc mô tả ý định tìm kiếm. Không chấp nhận các trường do LLM tự sinh như `allowed_units`, `max_classification`, `can_read` hoặc `authorization_result` làm quyết định có thẩm quyền.

### 7.3. Response Contract

Thành công:

```json
{
  "status": "answered",
  "answer": "Theo chính sách áp dụng cho phạm vi của bạn, ...",
  "applies_to": "verified_scope",
  "effective_at": "2026-01-01",
  "citations": [
    {
      "document_id": "leave_policy",
      "version_id": "leave_policy_v3",
      "chunk_id": "chunk_014",
      "title": "Chính sách nghỉ phép",
      "excerpt": "..."
    }
  ],
  "request_id": "req_example"
}
```

Không đủ bằng chứng:

```json
{
  "status": "abstained",
  "answer": "Tôi chưa đủ bằng chứng để xác định chính sách áp dụng. Vui lòng liên hệ đơn vị phụ trách.",
  "citations": [],
  "request_id": "req_example"
}
```

Đây là schema đề xuất cho MVP; backend cần định nghĩa Pydantic response models và thống nhất cách xử lý lỗi.

---

## 8. API Endpoints

| Method | Endpoint | Responsibility |
|---|---|---|
| `POST` | `/api/v1/ask` | Hỏi đáp trên tài liệu đã được phép truy cập |
| `GET` | `/api/v1/me` | Xem identity demo và context hiện tại |
| `POST` | `/api/v1/feedback` | Ghi nhận phản hồi câu trả lời |
| `GET` | `/api/v1/audit` | Truy vấn audit log theo quyền của người gọi |
| `GET` | `/api/v1/admin/users` | Danh sách tài khoản, memberships, đơn vị và role (Admin) |
| `POST` | `/api/v1/admin/users` | Tạo tài khoản và cấp membership/role ban đầu (Admin) |
| `PATCH` | `/api/v1/admin/users/{user_code}` | Khóa hoặc mở tài khoản (Admin) |
| `POST` | `/api/v1/admin/users/{user_code}/memberships` | Cấp thêm quyền theo đơn vị (Admin) |
| `PATCH` | `/api/v1/admin/memberships/{membership_id}` | Đổi role trên membership (Admin) |
| `DELETE` | `/api/v1/admin/memberships/{membership_id}` | Thu hồi membership (Admin) |
| `GET` | `/health` | Kiểm tra tình trạng ứng dụng |

Tài khoản Admin có giao diện quản trị để thực hiện các thao tác trên. API phía server vẫn kiểm tra role `security`; giao diện không thay thế kiểm tra quyền.

Evaluation ưu tiên chạy qua CLI (`backend/scripts/run_eval.py`). Chỉ mở `POST /api/v1/evaluate` khi thật sự cần; endpoint này phải được bảo vệ vì tốn tài nguyên và truy cập dữ liệu kiểm thử.

---

## 9. Evaluation Strategy

### 9.1. Authorization Tests

Mục tiêu quan trọng nhất là không để lộ dữ liệu trái phép. Các trường hợp:

- Người dùng được phép đọc tài liệu.
- Người dùng ngoài phạm vi tổ chức.
- Người dùng có nhiều membership/vai trò; yêu cầu đổi sang context không được phép.
- OpenFGA lỗi hoặc timeout (phải fail closed).
- Tài liệu thiếu metadata phân quyền.
- Tài liệu có classification vượt mức được phép.
- Tài liệu được cấp hoặc thu hồi quyền.
- Câu hỏi cố tình yêu cầu LLM bỏ qua kiểm soát quyền.

Tiêu chí bắt buộc: **không có chunk trái phép lọt qua ranh giới retrieval**. Không chỉ kiểm tra câu trả lời cuối, vì LLM có thể diễn đạt lại thông tin đã rò rỉ.

### 9.2. Temporal and Resolution Tests

- Truy vấn chính sách hiện hành và tại thời điểm lịch sử.
- Phiên bản chưa có hiệu lực, đã hết hiệu lực, bị thay thế, draft/chưa phê duyệt.
- Chính sách cấp cha bắt buộc và chính sách cấp con.
- Xung đột không thể giải quyết.
- Phiên bản mới tồn tại nhưng người dùng không được phép truy cập.

Khi không thể xác định phiên bản phù hợp một cách an toàn, hệ thống từ chối kết luận thay vì tự chọn.

### 9.3. Retrieval and Answer Quality

Các chỉ số: Recall@5, citation correctness, faithfulness, scope correctness, abstention precision, latency (đặc biệt p95).

| Metric | Initial target |
|---|---:|
| Unauthorized chunks at retrieval boundary | 0 |
| Scope correctness | ≥ 95% |
| Temporal correctness | ≥ 95% |
| Citation correctness | ≥ 95% |
| p95 latency | < 8 giây |

Đây là mục tiêu thiết kế ban đầu, không phải kết quả đã đo. Chỉ báo cáo đạt mục tiêu sau khi chạy evaluation trên bộ dữ liệu và môi trường đã xác định.

### 9.4. Golden Dataset

`data/eval/golden.jsonl` gồm: câu hỏi có đáp án rõ ràng; câu hỏi cần áp dụng phạm vi tổ chức; câu hỏi về phiên bản lịch sử; câu hỏi có xung đột giữa các chính sách; câu hỏi ngoài quyền; câu hỏi không đủ bằng chứng; câu hỏi cố bỏ qua kiểm soát quyền.

Mỗi trường hợp xác định đáp án kỳ vọng, persona, thời điểm truy vấn, nguồn được phép và hành vi mong đợi (trả lời, làm rõ hoặc từ chối).

---

## 10. Setup and Execution

### 10.1. Environment

Tạo `.env` từ `.env.example` ở thư mục gốc:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/wiki
OPENFGA_API_URL=http://openfga:8080
LLM_PROVIDER=gemini
GEMINI_API_KEY=
```

Đây là giá trị ví dụ; tên biến phải thống nhất với `config.py` và `docker-compose.yml`. Không commit `.env` hoặc API keys lên Git.

### 10.2. Start Services

```bash
docker compose up -d
```

Lệnh này chỉ khởi chạy các dịch vụ trong `docker-compose.yml` (PostgreSQL + pgvector, OpenFGA); không tự nạp schema, authorization model, tuples hay dữ liệu mẫu. Thực hiện theo thứ tự:

1. Khởi chạy PostgreSQL + pgvector và OpenFGA.
2. Áp dụng `backend/db/schema.sql` (hoặc `scripts/init_db.py`).
3. Nạp authorization model `authz/model.fga` vào OpenFGA.
4. Tạo/cấp dữ liệu ban đầu vào PostgreSQL; sau đó đồng bộ tuples hiện hành sang OpenFGA bằng `python -m app.authz.seed`.
5. Ingest tài liệu Markdown trong `data/documents/`.
6. Khởi chạy backend và frontend.
7. Chạy test phân quyền trước khi thử hỏi đáp.

### 10.3. Run Backend

```bash
cd backend
pip install -e .
uvicorn app.main:app --reload
```

Giả định `pyproject.toml` đã cấu hình package đúng. Production cần cấu hình riêng, không dùng `--reload`.

---

## 11. Implementation Roadmap

### Phase 1 — Foundation
- Docker Compose, PostgreSQL + pgvector, OpenFGA.
- Database schema; tài khoản, cây tổ chức, role assignments, authorization model và seed tuples.
- Kiểm thử OpenFGA và Policy Adapter.

### Phase 2 — Ingestion and Retrieval
- Markdown parser và metadata validation.
- Chunking theo cấu trúc điều khoản; embedding và indexing.
- Hybrid retrieval với bộ lọc quyền trước ranking.
- Kiểm thử không rò rỉ dữ liệu.

### Phase 3 — RAG and Temporal Resolution
- Structured query; quy tắc hiệu lực và phiên bản.
- Generation có citation; verification và abstention.
- Kiểm thử xung đột chính sách.

### Phase 4 — API, UI and Evaluation
- FastAPI endpoints; Chat UI, PersonaSwitcher, ContextBadge, SourceCard.
- Feedback và audit view.
- Golden dataset và báo cáo metrics; đo latency và kiểm tra các ca lỗi.

---

## 12. Security Principles

1. **Authorization before ranking:** quyền áp dụng trước khi tài liệu tham gia xếp hạng.
2. **Fail closed / default deny:** lỗi kiểm tra quyền hoặc thiếu metadata bắt buộc không dẫn đến truy cập mặc định.
3. **Trusted identity:** không tin danh tính hay quyền do client hoặc LLM tự khai báo.
4. **No permission decisions by LLM:** LLM không quyết định user có quyền đọc tài liệu hay không.
5. **Temporal correctness:** xác định phiên bản theo thời điểm truy vấn và quy tắc nghiệp vụ.
6. **Authorized evidence only:** chỉ đưa bằng chứng đã qua kiểm soát quyền vào prompt.
7. **Auditable decisions:** ghi quyết định, request ID và nguồn liên quan vào `audit_log` append-only, theo chính sách logging.
8. **Test the retrieval boundary:** kiểm thử dữ liệu trái phép ngay tại tầng truy xuất, không chỉ ở câu trả lời cuối.

---

## 13. Future Improvements

- Tích hợp OIDC/SSO và đồng bộ dữ liệu nhân sự (HRIS).
- Mở rộng bộ tài liệu (150–300) và golden dataset (100–150 câu).
- Tối ưu tìm kiếm tiếng Việt và cấu hình BM25.
- Bật reranker để cải thiện thứ hạng.
- Tối ưu cache nhưng cache key và invalidation phải tôn trọng quyền.
- Bổ sung monitoring, rate limiting, audit retention.
- Tách module khi logic nghiệp vụ đủ phức tạp để cần triển khai hoặc kiểm thử độc lập.

**Nguyên tắc thiết kế:** giữ code MVP đơn giản, nhưng không gộp hoặc bỏ qua các ranh giới quan trọng giữa identity, authorization, retrieval, temporal resolution và generation.