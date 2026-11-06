# Permission-Aware RAG Wiki

Wiki nội bộ hỏi đáp trên tài liệu doanh nghiệp bằng Retrieval-Augmented Generation (RAG). Hệ thống giới hạn bằng chứng theo đơn vị tổ chức, membership/context đang chọn, clearance, trạng thái phê duyệt và ngày hiệu lực của tài liệu. Câu trả lời có trích dẫn; nếu không đủ bằng chứng hoặc không xác minh được quyền, hệ thống từ chối thay vì đoán.

> **Trạng thái:** MVP. Người dùng đăng nhập bằng tài khoản và mật khẩu do Admin cấp; backend xác minh mật khẩu với password hash trong PostgreSQL. Sau đăng nhập, frontend gửi `X-Persona-Id` để chỉ định tài khoản cho các API. Cơ chế này chưa có session/JWT hoặc OIDC/SSO nên chưa phù hợp để đưa dịch vụ ra Internet hay xử lý dữ liệu nhạy cảm thật.

## Mục lục

1. [Tính năng](#1-tính-năng)
2. [Kiến trúc hệ thống](#2-kiến-trúc-hệ-thống)
3. [Cấu trúc repository](#3-cấu-trúc-repository)
4. [Mô hình danh tính và phân quyền](#4-mô-hình-danh-tính-và-phân-quyền)
5. [Luồng xử lý RAG](#5-luồng-xử-lý-rag)
6. [Dữ liệu và schema](#6-dữ-liệu-và-schema)
7. [Chuẩn tài liệu đầu vào](#7-chuẩn-tài-liệu-đầu-vào)
8. [Cài đặt và khởi chạy trên Windows](#8-cài-đặt-và-khởi-chạy-trên-windows)
9. [Khởi tạo organization units, users và OpenFGA](#9-khởi-tạo-organization-units-users-và-openfga)
10. [Ingest tài liệu](#10-ingest-tài-liệu)
11. [Frontend và các giao diện](#11-frontend-và-các-giao-diện)
12. [API](#12-api)
13. [Evaluation và kiểm thử](#13-evaluation-và-kiểm-thử)
14. [Vận hành, reset và xử lý sự cố](#14-vận-hành-reset-và-xử-lý-sự-cố)
15. [Bảo mật và giới hạn](#15-bảo-mật-và-giới-hạn)

---

## 1. Tính năng

- Hỏi đáp tiếng Việt trên tài liệu Markdown đã ingest.
- Tìm kiếm kết hợp PostgreSQL full-text search và vector search với pgvector, sau đó hợp nhất kết quả bằng Reciprocal Rank Fusion (RRF).
- Tính quyền theo quan hệ tổ chức bằng OpenFGA (ReBAC), kết hợp clearance và thuộc tính tài liệu (ABAC).
- Hỗ trợ nhiều membership/context cho cùng một user; mỗi truy vấn chỉ sử dụng một context.
- Lọc tài liệu theo ngày hiệu lực, trạng thái, phê duyệt và quan hệ phiên bản như `supersedes`/`amends`.
- Citation được đối chiếu với tập chunk thực sự cung cấp cho bước sinh câu trả lời; câu trả lời có thể từ chối hoặc yêu cầu chọn context.
- Lưu users, organization units, memberships, tài liệu, versions, chunks, hội thoại, nguồn hội thoại, audit log và outbox trong PostgreSQL.
- Admin Portal cấp tài khoản, xem danh sách users/organization units/roles, quản lý memberships và role, bật/tắt tài khoản.
- Knowledge Owner Portal xem tài liệu cần rà soát và ghi nhận quyết định đối chiếu chính sách.
- CLI ingest và evaluation; bộ `golden.jsonl` và `authz_bypass.jsonl` phục vụ kiểm thử phạm vi, quyền, thời gian và prompt injection.

## 2. Kiến trúc hệ thống

Đây là **modular monolith kết hợp các dịch vụ hạ tầng độc lập**, không phải hệ microservices. Một ứng dụng FastAPI đăng ký các router theo chức năng và điều phối pipeline RAG. PostgreSQL và OpenFGA chạy thành các container/dịch vụ riêng; frontend React là ứng dụng web riêng.

| Thành phần | Công nghệ | Trách nhiệm |
|---|---|---|
| Frontend | React, TypeScript, Vite | Đăng nhập bằng tài khoản được Admin cấp, chat, chọn context, quản trị tài khoản/membership, Knowledge Owner Portal |
| Backend | Python, FastAPI | API, xác thực tài khoản demo, điều phối RAG, audit, admin và owner workflows |
| Database | PostgreSQL 16 | Identity, cây tổ chức, memberships, roles, tài liệu, chunks, hội thoại và audit |
| Vector index | pgvector | Embedding và tìm kiếm vector |
| Authorization service | OpenFGA | Quan hệ ReBAC: membership được gán vào đơn vị và kế thừa viewer theo cây |
| Embedding | BGE-M3; fake hash embedder cho dev/test | Mã hóa chunks/câu hỏi thành vector |
| LLM | Gemini khi có API key; extractive fallback khi không có key | Phân tích câu hỏi và sinh nội dung dựa trên bằng chứng đã lọc |
| Local orchestration | Docker Compose | Khởi chạy PostgreSQL/pgvector và OpenFGA |

### Sơ đồ tổng quát

```text
React/Vite
    │ HTTP API + X-Persona-Id (demo)
    ▼
FastAPI modular monolith
    ├── Auth / Context ───── PostgreSQL
    ├── Admin / Owner / Audit ─ PostgreSQL
    └── RAG pipeline
          ├── Query understanding
          ├── OpenFGA + ABAC policy adapter
          ├── PostgreSQL FTS + pgvector retrieval (lọc quyền tại SQL)
          ├── Temporal/policy resolution
          ├── LLM hoặc extractive fallback
          └── Citation verification + audit
```

### Hạ tầng local

[`docker-compose.yml`](./docker-compose.yml) khởi chạy:

- `postgres`: image `pgvector/pgvector:pg16`, cổng mặc định `5432`; lưu database ứng dụng và database OpenFGA trên cùng PostgreSQL server.
- `openfga-migrate`: chạy migration datastore OpenFGA và kết thúc thành công.
- `openfga`: OpenFGA HTTP `8080`, gRPC `8081`, Playground `3000`.

Compose **không** tự tạo schema ứng dụng, tài khoản/persona, cây đơn vị, memberships, tài liệu hoặc authorization model/tuples. Các phần đó cần thiết lập theo các bước bên dưới.

## 3. Cấu trúc repository

```text
wiki/
├── README.md
├── RUN.md                         # Hướng dẫn chạy ngắn gọn
├── .env.example                   # Mẫu biến môi trường
├── docker-compose.yml             # PostgreSQL/pgvector + OpenFGA
├── docker/postgres/init/          # Tạo database OpenFGA khi volume PostgreSQL mới
├── data/
│   ├── org.yaml                   # Tham chiếu dữ liệu demo cũ; không phải runtime source
│   ├── documents/                 # 33 tài liệu Markdown mẫu
│   └── eval/
│       ├── golden.jsonl           # Bộ câu hỏi chuẩn hiện có
│       └── authz_bypass.jsonl     # Bộ kiểm thử quyền và truy cập ngoài phạm vi
├── backend/
│   ├── pyproject.toml
│   ├── db/schema.sql              # Schema PostgreSQL chuẩn
│   ├── app/
│   │   ├── main.py                # Tạo FastAPI app và dependency container
│   │   ├── config.py              # Đọc cấu hình từ .env ở repository root
│   │   ├── db.py                  # Pool, schema management, DB health check
│   │   ├── auth.py                # Tài khoản, mật khẩu, persona, membership/context
│   │   ├── authz/
│   │   │   ├── model.fga          # OpenFGA model
│   │   │   ├── seed.py            # Tạo store/model và đồng bộ tuples/outbox
│   │   │   ├── bootstrap.py       # Tạo security admin và root unit lần đầu
│   │   │   └── policy_adapter.py  # OpenFGA ReBAC + ABAC thành retrieval scope
│   │   ├── api/                   # Query, admin, owner, audit, feedback APIs
│   │   ├── ingest/ingest.py       # Parse, validate, chunk, embed và persist
│   │   └── rag/                   # Query, retrieval, resolution, generation
│   ├── scripts/run_eval.py        # Chạy evaluation suite
│   └── tests/                    # Unit và integration tests
└── frontend/
    ├── package.json
    └── src/
        ├── App.tsx
        ├── api.ts
        └── components/
            ├── ChatPage.tsx
            ├── AdminPortal.tsx
            ├── OwnerReviewPortal.tsx
            ├── PersonaSwitcher.tsx
            ├── ContextBadge.tsx
            └── SourceCard.tsx
```

`data/org.yaml` chỉ là tài liệu tham khảo cho dữ liệu demo và test. Runtime đọc organization units, users, memberships và roles từ PostgreSQL; reset database không tự nhập lại file YAML này.

## 4. Mô hình danh tính và phân quyền

### 4.1. User, membership và context

- `users` là tài khoản đăng nhập.
- `memberships` gắn user với một organization unit, `context_id`, loại membership và chức danh.
- `role_assignments` gắn role và clearance với membership.
- `context_id` là ngữ cảnh làm việc được chọn cho một truy vấn, không phải một database hay role.
- Người có nhiều membership phải chọn một context cho từng câu hỏi. Hệ thống không cộng quyền giữa các context.

Ví dụ người kiêm nhiệm có thể có context `ctx_sales_a` trong Công ty A và `ctx_qa_b` trong Công ty B. Khi truy vấn bằng `ctx_qa_b`, quyền chỉ được tính từ membership đó.

### 4.2. Cây tổ chức

`organization_units` dùng `unit_code`, `name`, `unit_type`, `parent_unit_id` và `status`. Các loại hiện được schema hỗ trợ:

`group`, `company`, `division`, `department`, `team`, `project`, `committee`.

Quy ước cây tổ chức khuyến nghị:

| Loại đơn vị mới | Loại parent được phép |
|---|---|
| `group` | Không có parent |
| `company` | `group` |
| `division` | `company` |
| `department` | `company` hoặc `division` |
| `team` | `department` |
| `project`, `committee` | `group`, `company`, `division`, `department` hoặc `team` |

Bootstrap tạo sẵn root unit `root` loại `group`. Security admin có thể tạo unit con và chỉnh sửa tên, parent, trạng thái qua Admin Portal. `unit_code` và `unit_type` cố định sau khi tạo. Backend kiểm tra loại parent, đơn vị cha active và ngăn chuyển unit xuống dưới chính hậu duệ của nó.

### 4.3. ReBAC và ABAC

OpenFGA quản lý quan hệ:

- User được gán vào unit qua relation `assigned`.
- Context đang dùng được truyền như contextual tuple `member`.
- Unit con liên kết với parent qua relation `child`.
- `viewer` được kế thừa từ unit đang chọn lên các đơn vị cấp trên.

Policy Adapter kết hợp kết quả OpenFGA với clearance của role và thuộc tính tài liệu trong PostgreSQL. Mức classification theo thứ tự:

`public` < `internal` < `confidential` < `restricted`.

Role mặc định trong schema:

| Role | Clearance tối đa |
|---|---|
| `employee` | `internal` |
| `manager` | `confidential` |
| `hr_owner` | `confidential` |
| `security` | `restricted` |

Một chunk chỉ được truy xuất khi unit sở hữu nằm trong đơn vị được phép, clearance đủ, version đã được duyệt, trạng thái được phép và thời điểm truy vấn nằm trong khoảng hiệu lực. Truy vấn lịch sử có thể lấy phiên bản `superseded`/`expired` nếu nó từng có hiệu lực tại ngày được hỏi và vẫn vượt qua các kiểm tra quyền.

Mọi lỗi không xác định được quyền (ví dụ OpenFGA không hoạt động, context không được gán hoặc thiếu metadata) phải dẫn đến từ chối/fail closed. LLM không quyết định quyền và không nhận chunk trước khi lọc.

## 5. Luồng xử lý RAG

### 5.1. Ingestion offline

```text
Markdown + YAML front matter
    → kiểm tra metadata và unit trong PostgreSQL
    → chunk theo Điều/Khoản
    → tạo embedding
    → ghi documents / document_versions / chunks
    → tạo quan hệ phiên bản
```

Tài liệu lỗi metadata bị từ chối ingest. `--dry-run` cho phép quét và validate mà không ghi dữ liệu. `--force` buộc nạp lại tài liệu. `--fake` dùng hash embedder cho ingest dev/test.

### 5.2. Hỏi đáp online

```text
X-Persona-Id + context đã chọn
    → parse/chuẩn hóa câu hỏi
    → xác minh membership và clearance
    → OpenFGA xác định allowed units
    → PostgreSQL full-text + pgvector, lọc quyền/hiệu lực tại SQL
    → RRF
    → resolution hiệu lực và quan hệ chính sách
    → LLM/extractive fallback tạo claims và citations
    → xác minh chunk/quote citation
    → audit và trả lời hoặc abstain
```

Question understanding có thể dùng Gemini nếu đã cấu hình API key; nếu không, hệ thống dùng heuristic. Khi không có Gemini API key, `ExtractiveJsonLLM` tạo phản hồi rút trích bảo thủ từ evidence. Đây là fallback demo, không có chất lượng tương đương mô hình sinh đầy đủ.

Citation phải trỏ tới chunk có trong evidence đầu vào. Khi tài liệu bị chặn hoặc không tìm thấy trong phạm vi, thông báo công khai được thiết kế chung để tránh xác nhận nội dung/tài liệu ẩn.

## 6. Dữ liệu và schema

Schema chuẩn là [`backend/db/schema.sql`](./backend/db/schema.sql), được áp dụng bằng `python -m app.db init`. Database ứng dụng có các nhóm bảng:

| Nhóm | Bảng | Mục đích |
|---|---|---|
| Danh tính và tổ chức | `users`, `organization_units`, `memberships`, `roles`, `role_assignments` | Tài khoản, cây đơn vị, context và role có hiệu lực |
| Tài liệu | `documents`, `document_versions`, `document_version_relations` | Mã tài liệu, phiên bản, metadata hiệu lực và quan hệ giữa versions |
| Retrieval | `chunks` | Nội dung chunk, embedding vector, full-text vector và trạng thái active |
| Hội thoại | `conversations`, `messages`, `message_sources` | Lịch sử hỏi đáp và liên kết nguồn |
| Audit/vận hành | `audit_log`, `ingestion_jobs`, `authz_outbox` | Audit append-only, ingest jobs và đồng bộ OpenFGA |

Schema tạo các extension PostgreSQL `vector`, `unaccent`, `btree_gist`. Embedding column hiện được định nghĩa `VECTOR(1024)`; nếu đổi `EMBEDDING_DIM`, cần thay schema phù hợp và khởi tạo lại DB/index một cách có chủ đích.

`python -m app.db init` là thao tác khởi tạo/idempotent schema, không nạp dữ liệu demo. Nếu phát hiện một schema legacy, lệnh dừng và yêu cầu xử lý migration thay vì tự ghi đè.

### Trạng thái tài liệu

Front matter dùng trạng thái `draft`, `active`, `superseded`, `expired`. Khi persist, `active` được lưu dưới trạng thái DB `published`; retrieval loại draft/chưa approved. Các mốc hiệu lực lưu vào `valid_from` và `valid_to`.

## 7. Chuẩn tài liệu đầu vào

Mỗi file trong `data/documents/` là Markdown UTF-8, có YAML front matter. `document_id` phải trùng tên file không gồm `.md`. Trường bắt buộc:

| Trường | Ý nghĩa |
|---|---|
| `document_id` | Mã tài liệu dạng chữ thường/số/gạch nối, trùng tên file |
| `title` | Tiêu đề hiển thị |
| `owner_unit` | `unit_code` đã tồn tại và đang active trong PostgreSQL |
| `scope` | Phải khớp `unit_type` của `owner_unit` |
| `doc_type` | Một trong `policy`, `process`, `guideline`, `faq` |
| `version` | Nhãn version |
| `effective_from` | Ngày ISO `YYYY-MM-DD` |
| `status` | `draft`, `active`, `superseded` hoặc `expired` |
| `classification` | `public`, `internal`, `confidential`, `restricted` |
| `mandatory` | Boolean YAML `true`/`false` |

Các trường tùy chọn gồm `logical_document_id`, `effective_to`, `topic`, `parent_conformance`, `policy_values`, `supersedes`, `amends`. Các quan hệ trong `supersedes`/`amends` tham chiếu `document_id` khác trong corpus. `parent_conformance` nhận `confirmed`, `unverified` hoặc `conflicting`.

Nội dung được chunk theo tiêu đề Điều (ví dụ `## Điều 1. ...`) và các khoản đánh số. Mỗi tài liệu cần có organization unit trước khi ingest. Parser không lấy hoặc tự tạo đơn vị từ `org.yaml`.

Ví dụ:

```yaml
---
document_id: dept-leave-2025
title: "Quy định nghỉ phép – Phòng Kỹ thuật"
owner_unit: a_tech
scope: department
doc_type: policy
topic: nghi_phep
version: "2025"
effective_from: "2025-01-01"
effective_to: null
status: active
classification: internal
mandatory: false
supersedes: []
amends: []
---

# Quy định nghỉ phép

## Điều 1. Số ngày phép
1. Nội dung quy định...
```

Trong ví dụ, `a_tech` phải tồn tại trong `organization_units` với `unit_type = department`.

## 8. Cài đặt và khởi chạy trên Windows

### 8.1. Điều kiện

- Windows với Docker Desktop đang chạy **Linux containers**.
- Python 3.11 trở lên; repository có thể dùng virtual environment `backend\.venv`.
- Node.js/npm tương thích với package frontend.
- Kết nối mạng khi tải Docker images, Python/npm packages, BGE-M3 model hoặc gọi Gemini.

Nếu `docker compose` báo không kết nối được `dockerDesktopLinuxEngine`, khởi động Docker Desktop trước; lệnh `docker info` phải truy cập daemon thành công.

### 8.2. Cấu hình môi trường

Tại repository root, tạo `.env` từ `.env.example`, sau đó điền các giá trị phù hợp. Không commit `.env` hoặc API key.

Các biến quan trọng:

| Biến | Giá trị mặc định/mục đích |
|---|---|
| `POSTGRES_USER` | `wiki` |
| `POSTGRES_PASSWORD` | Mật khẩu local; phải thay đổi ngoài môi trường dev |
| `POSTGRES_DB` | `premission_wiki` |
| `POSTGRES_HOST` / `POSTGRES_PORT` | Mặc định `localhost` / `5432` cho backend chạy trên host |
| `OPENFGA_API_URL` | Mặc định `http://localhost:8080` cho backend trên host |
| `OPENFGA_STORE_ID`, `OPENFGA_MODEL_ID` | Có thể để trống trong local flow; seed tạo store/model |
| `GEMINI_API_KEY` | Không có thì dùng extractive fallback |
| `GEMINI_MODEL` hoặc `LLM_MODEL` | Tên model Gemini |
| `EMBEDDING_MODEL` | Mặc định `BAAI/bge-m3` |
| `EMBEDDING_DIM` | Mặc định `1024`, phải khớp schema |
| `APP_ENV` | Mặc định `dev`; `reset` chỉ được phép ở `dev` |

Các biến cho Docker Compose như `OPENFGA_HTTP_PORT` có thể override port publish. Khi backend cũng chạy trong container, hostname `localhost` cần đổi thành hostname dịch vụ Docker phù hợp.

### 8.3. Khởi động dịch vụ và schema

Tại repository root:

```powershell
docker compose up -d
docker compose ps
```

Sau đó tạo môi trường Python và cài backend (trong PowerShell):

```powershell
Set-Location D:\project_dads\wiki\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

Khởi tạo schema:

```powershell
.\.venv\Scripts\python.exe -m app.db init
```

Kiểm tra PostgreSQL và OpenFGA:

```powershell
.\.venv\Scripts\python.exe -m app.db check
```

`init` không xóa dữ liệu hiện có và không tạo organization units/users/memberships. Nếu lệnh dừng vì legacy schema, không chạy reset nếu chưa chấp nhận mất dữ liệu; cần backup và migration theo schema đang dùng.

## 9. Khởi tạo organization units, users và OpenFGA

### 9.1. Bootstrap security admin

Với database mới, tạo tài khoản security admin đầu tiên:

```powershell
Set-Location D:\project_dads\wiki\backend
.\.venv\Scripts\python.exe -m app.authz.bootstrap
```

Chương trình yêu cầu nhập mật khẩu hai lần, tối thiểu 12 ký tự; mật khẩu không được in ra terminal. Không có mật khẩu mặc định. Bootstrap tạo user `admin`, email `admin@permission-aware-wiki.local`, root organization unit `root` và security membership `bootstrap:admin`. Lệnh không ghi đè admin hoặc chạy lại khi đã có security admin active.

### 9.2. Tạo và chỉnh sửa cây organization units

Bootstrap tạo unit gốc `root` loại `group`. Đăng nhập frontend bằng tài khoản bootstrap rồi mở Admin Portal. Trong phần **Cây đơn vị tổ chức**, security admin có thể:

- Tạo unit với mã, tên, loại và parent.
- Chỉnh sửa tên, parent hoặc trạng thái; mã và loại unit không đổi.
- Xem cả đơn vị active và inactive theo dạng cây.

Parent phải theo quy ước ở [mục 4.2](#42-cây-tổ-chức). Không thể chuyển unit xuống dưới chính hậu duệ của nó. Không thể ngừng hoạt động unit còn đơn vị con active hoặc membership còn hiệu lực. Thao tác được ghi audit và quan hệ parent được đồng bộ OpenFGA qua outbox. Nếu kết quả báo `authorization_sync: pending`, kiểm tra OpenFGA trước khi dùng quyền mới.

Sau database reset, bootstrap lại admin rồi tạo cây đơn vị trong Admin Portal. Các đơn vị phải được tạo trước khi cấp membership hoặc ingest tài liệu.

### 9.3. Seed OpenFGA

```powershell
Set-Location D:\project_dads\wiki\backend
.\.venv\Scripts\python.exe -m app.authz.seed
```

Seed tạo lại store có tên mặc định `permission-aware-rag-wiki`, ghi authorization model và nạp tuple từ cây đơn vị/memberships active. **Lệnh này xóa store OpenFGA cùng tên trước khi tạo lại.** Chỉ chạy khi chấp nhận tái tạo store; không dùng như thao tác thường xuyên trên production. Các thay đổi unit và membership thông thường từ Admin Portal được đồng bộ qua outbox; seed dùng để khởi tạo hoặc dựng lại toàn bộ store.

### 9.4. Tạo tài khoản và membership

Chạy backend và frontend theo [mục 11](#11-frontend-và-các-giao-diện), đăng nhập Admin Portal bằng thông tin bootstrap. Hãy seed OpenFGA sau khi provision cây tổ chức và trước khi dùng các tài khoản mới để truy vấn; nếu vừa tạo lại store thì cần seed lại.

Admin Portal chỉ cho tài khoản có role `security`. Tại đây Admin có thể:

- Tạo tài khoản active với mã tài khoản, tên, email, mật khẩu ban đầu (tối thiểu 12 ký tự), một membership/context, unit, role, chức danh và loại membership.
- Xem users, memberships, role assignments, các unit active và danh sách roles.
- Cấp membership/context/role bổ sung cho user active (ví dụ người kiêm nhiệm).
- Thay role hoặc thu hồi membership; bật lại hoặc vô hiệu hóa tài khoản.

Mỗi `context_id` phải duy nhất. Unit được gán phải tồn tại và active; role phải có trong bảng `roles`. Tài khoản và membership được ghi audit; thay đổi membership được đưa vào outbox để đồng bộ quan hệ user-unit với OpenFGA. API bảo vệ security admin cuối cùng khỏi bị vô hiệu hóa hoặc mất role. Admin Portal hiện không sửa email, reset mật khẩu của người khác, cũng không quản lý organization units.

### 9.5. Cấp user/membership qua API

Các thao tác quản trị được mô tả trong [API](#12-api), bao gồm `POST /api/v1/admin/users`, `POST /api/v1/admin/users/{user_code}/memberships`, thay đổi role và thu hồi membership. User không có membership active sẽ không có context để hỏi đáp.

## 10. Ingest tài liệu

### 10.1. Cài dependency ML

Để dùng BGE-M3 cho ingest:

```powershell
Set-Location D:\project_dads\wiki\backend
.\.venv\Scripts\python.exe -m pip install -e ".[ml]"
```

Tải model BGE-M3 lần đầu có thể tốn thời gian và dung lượng. `--fake` dùng deterministic hash embedder, phù hợp kiểm tra luồng ingest chứ không đem lại chất lượng semantic search tương đương BGE-M3.

### 10.2. Kiểm tra và nạp

Quét/validate mà không ghi DB:

```powershell
.\.venv\Scripts\python.exe -m app.ingest.ingest run --dry-run
```

Nạp hoặc cập nhật tài liệu:

```powershell
.\.venv\Scripts\python.exe -m app.ingest.ingest run
```

Buộc nạp lại:

```powershell
.\.venv\Scripts\python.exe -m app.ingest.ingest run --force
```

Chạy ingest với fake embeddings:

```powershell
.\.venv\Scripts\python.exe -m app.ingest.ingest run --fake --force
```

Khi dùng fake embeddings, đặt `EMBEDDING_BACKEND=fake` khi chạy backend để query embedder khớp với dữ liệu ingest. Nếu dùng BGE-M3 thì `EMBEDDING_MODEL` và `EMBEDDING_DIM` phải thống nhất giữa ingest, backend và schema.

Lệnh ingest yêu cầu đã có organization units trong PostgreSQL. Nếu database mới/reset, tạo cây tổ chức trong Admin Portal theo [mục 9.2](#92-tạo-và-chỉnh-sửa-cây-organization-units) và kiểm tra mọi `owner_unit` trước khi ingest. Ingest không tự seed units từ `data/org.yaml`.

## 11. Frontend và các giao diện

### 11.1. Chạy backend

Từ thư mục `backend`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Địa chỉ local:

- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Health: `http://localhost:8000/health`

### 11.2. Chạy frontend

Mở PowerShell terminal khác:

```powershell
Set-Location D:\project_dads\wiki\frontend
npm install
npm run dev
```

Vite mặc định mở `http://localhost:8443`. Frontend dùng `VITE_API_BASE_URL` nếu được cấu hình; nếu không, mặc định gọi `http://localhost:8000`.

### 11.3. Giao diện

- **Chat:** hỏi đáp, hiển thị citation/nguồn và context đang dùng.
- **Persona/context:** tài khoản và context active lấy từ API/database; người kiêm nhiệm chọn một context cho mỗi yêu cầu.
- **Admin Portal:** chỉ role `security`; tạo/chỉnh sửa cây organization units, tạo tài khoản, quản lý memberships/roles và bật/tắt tài khoản.
- **Knowledge Owner Portal:** role `hr_owner` hoặc `security`; rà soát `parent_conformance`, xem bằng chứng và ghi nhận kết quả.

Người dùng không tự đăng ký: security admin tạo tài khoản trong Admin Portal, gán membership/context và role, đồng thời đặt mật khẩu ban đầu. Form giao diện đăng nhập bằng email; endpoint backend cũng chấp nhận mã tài khoản. Backend xác minh password hash và trả thông tin tài khoản/context. Giao diện gọi `/api/v1/personas` để hiển thị tài khoản active và điền email vào form. Nếu API này không tải được, frontend có danh sách tài khoản mẫu dự phòng để hiển thị; đăng nhập vẫn được xác minh bởi backend và không thể đăng nhập bằng tài khoản mẫu không có trong database.

Người dùng có thể tự đổi mật khẩu qua `POST /api/v1/auth/change-password` bằng cách cung cấp username/email, mật khẩu hiện tại và mật khẩu mới (tối thiểu 12 ký tự). Admin Portal hiện không có chức năng reset mật khẩu người khác.

Đăng nhập hiện chưa cấp session/JWT. Sau khi xác thực, frontend gửi `X-Persona-Id` trong các API request; backend dùng giá trị này để tìm tài khoản active và kiểm tra quyền cho endpoint. Vì header không phải credential được ký và có thể bị giả mạo bởi client, cơ chế này vẫn là giới hạn bảo mật của MVP, không phải xác thực production.

## 12. API

FastAPI định nghĩa schema request/response tại backend; xem chi tiết và thử endpoint trong Swagger UI.

### 12.1. Authentication, identity và chat

| Method | Endpoint | Mô tả |
|---|---|---|
| `GET` | `/api/v1/personas` | Danh sách tài khoản/membership active dùng để hiển thị context và điền email trên màn hình đăng nhập |
| `POST` | `/api/v1/auth/login` | Xác thực email hoặc username và mật khẩu tài khoản Admin đã cấp |
| `POST` | `/api/v1/auth/change-password` | Đổi mật khẩu sau khi xác minh mật khẩu hiện tại |
| `GET` | `/api/v1/me` | Persona và các context hiện có |
| `POST` | `/api/v1/ask` | Hỏi đáp theo context |
| `GET` | `/api/v1/conversations` | Danh sách hội thoại của persona |
| `GET` | `/api/v1/conversations/{conversation_id}` | Chi tiết hội thoại của persona |
| `POST` | `/api/v1/feedback` | Ghi phản hồi câu trả lời |

Ví dụ request:

```http
POST /api/v1/ask
X-Persona-Id: employee-a
Content-Type: application/json
```

```json
{
  "question": "Quy định nghỉ phép năm hiện hành là gì?",
  "context_id": "ctx_employee_a",
  "as_of": null,
  "conversation_id": null
}
```

`context_id` phải thuộc persona active; nếu persona có nhiều context mà không chọn context, API yêu cầu làm rõ. `as_of` là ngày ISO hoặc `null` cho thời điểm hiện tại. Response có thể có trạng thái `answered`, `abstained`, `needs_clarification` hoặc `answer_with_escalation`, citation, request ID và danh sách context khi cần chọn.

### 12.2. Admin API

Mọi endpoint dưới đây yêu cầu `X-Persona-Id` của security admin; server kiểm tra role và trạng thái active.

| Method | Endpoint | Mô tả |
|---|---|---|
| `GET` | `/api/v1/admin/users` | Danh sách users, memberships, units và roles |
| `POST` | `/api/v1/admin/users` | Cấp tài khoản, mật khẩu ban đầu, membership/context và role |
| `PATCH` | `/api/v1/admin/users/{user_code}` | Kích hoạt hoặc khóa user |
| `GET` | `/api/v1/admin/organization-units` | Danh sách units, parent và trạng thái |
| `POST` | `/api/v1/admin/organization-units` | Tạo organization unit |
| `PATCH` | `/api/v1/admin/organization-units/{unit_code}` | Sửa tên, parent hoặc trạng thái; không sửa code/type |
| `POST` | `/api/v1/admin/users/{user_code}/memberships` | Cấp membership/context/role bổ sung |
| `PATCH` | `/api/v1/admin/memberships/{membership_id}` | Đổi role membership |
| `DELETE` | `/api/v1/admin/memberships/{membership_id}` | Thu hồi membership |

Các endpoint Admin yêu cầu `X-Persona-Id` của security admin; server kiểm tra role và trạng thái active. Các unit chỉ sửa tên/parent/status; code và type là bất biến. Backend từ chối parent sai loại hoặc tạo chu trình, và từ chối ngừng unit còn child active/membership còn hiệu lực. Các thay đổi quản trị được ghi audit; thay đổi cây unit và membership được đồng bộ OpenFGA qua outbox. Nếu `authorization_sync` trả `pending`, cần kiểm tra OpenFGA và xử lý outbox trước khi dựa vào quyền vừa thay đổi.

### 12.3. Knowledge Owner và audit

| Method | Endpoint | Mô tả |
|---|---|---|
| `GET` | `/api/v1/owner/pending-reviews` | Xem tài liệu pending/reviewed/all; chỉ `hr_owner` hoặc `security` |
| `POST` | `/api/v1/owner/reviews/{document_id}` | Ghi nhận `confirmed`, `conflicting` hoặc `unverified` kèm lý do |
| `GET` | `/api/v1/audit` | Truy vấn audit log theo quyền và bộ lọc của API |
| `GET` | `/health` | Health endpoint ứng dụng |

## 13. Evaluation và kiểm thử

### 13.1. Bộ dữ liệu

- [`data/eval/golden.jsonl`](./data/eval/golden.jsonl): câu hỏi có đáp án mong đợi, nguồn, persona/context và ngày truy vấn.
- [`data/eval/authz_bypass.jsonl`](./data/eval/authz_bypass.jsonl): 50 ca kiểm thử quyền, truy cập chéo tổ chức, clearance, context, prompt injection, cùng các ca đối chứng được phép.

Các suite là dữ liệu vào cho evaluation CLI; chúng **không** được gửi nguyên bộ vào prompt LLM.

### 13.2. Điều kiện trước khi chạy

Evaluation kết nối trực tiếp PostgreSQL/OpenFGA qua cấu hình hiện tại và gọi pipeline, không cần chạy endpoint HTTP của backend. Cần:

- schema database đã init;
- organization units, users, memberships, roles/context tương ứng đã tồn tại và active;
- dữ liệu tài liệu đã ingest;
- OpenFGA store/model/tuples đã seed;
- embedding backend/runtime phù hợp với embedding đã lưu;
- Gemini key nếu muốn dùng Gemini; nếu không sẽ dùng fallback.

`data/org.yaml` không tự được load thành persona runtime. Bộ eval có user/context ID mẫu; cần provision các ID đó vào PostgreSQL nếu muốn chạy nguyên suite.

### 13.3. Chạy evaluation

Từ thư mục `backend`:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_eval `
  --suite ..\data\eval\authz_bypass.jsonl `
  --out ..\data\eval\authz_bypass_results.jsonl `
  --meta ..\data\eval\authz_bypass_meta.json
```

Chạy một ca:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_eval `
  --suite ..\data\eval\authz_bypass.jsonl `
  --id AB001 `
  --out ..\data\eval\ab001_results.jsonl `
  --meta ..\data\eval\ab001_meta.json
```

CLI in kết quả theo ca và summary gồm unauthorized chunks, scope/status correctness, temporal correctness, citation correctness, precision/recall của abstain và latency. Kết quả được lưu trong JSONL cùng metadata JSON.

Lưu ý: metric `scope_correct` so sánh status và nguồn được cite với kỳ vọng; cần đọc từng record và các metric retrieval/citation để đánh giá đầy đủ. Test bộ API và test unit không thay thế benchmark trên database/dịch vụ thực tế.

### 13.4. Chạy unit tests

Từ `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Một số test authorization cần PostgreSQL/OpenFGA và có thể được skip nếu môi trường chưa chạy.

## 14. Vận hành, reset và xử lý sự cố

### Kiểm tra database/service

```powershell
docker compose ps
Set-Location D:\project_dads\wiki\backend
.\.venv\Scripts\python.exe -m app.db check
```

### `relation "users" does not exist`

Kiểm tra backend đang kết nối đúng database qua `POSTGRES_DB`, sau đó chạy `python -m app.db init`. Eval yêu cầu cả schema lẫn users/context active.

### Legacy database schema detected

Thông báo này có nghĩa database có bảng thuộc schema cũ nhưng không khớp schema hiện tại; không đồng nghĩa database chưa tồn tại. Hãy sao lưu và migration dữ liệu. Không dùng `reset` nếu chưa chấp nhận mất dữ liệu.

### Ingest báo chưa có organization units

Tạo organization units trong Admin Portal trước. Ingest cần `owner_unit` trong mỗi file tài liệu khớp với unit active trong database; YAML demo không được load tự động.

### Lỗi 422 khi tạo organization unit/tài khoản/membership

Xem response body `detail` trong Browser DevTools → Network. Khi tạo unit, kiểm tra mã hợp lệ/không trùng, parent đang active và loại parent phù hợp (ví dụ `team` phải dưới `department`, `division` dưới `company`). Khi đổi parent, không được chọn hậu duệ; khi ngừng hoạt động, unit không được có child active hoặc membership còn hiệu lực. Khi tạo tài khoản/membership, kiểm tra unit active, role hợp lệ, email/mã/context chưa bị dùng.

### Reset database

`python -m app.db reset` chỉ được cho phép khi `APP_ENV=dev`. Thao tác này **xóa các bảng dữ liệu ứng dụng rồi tạo schema lại**; users, units, memberships, tài liệu, chunks và audit sẽ mất. Sau reset, chạy lại init/bootstrap, tạo organization units trong Admin Portal, seed OpenFGA, tạo users/memberships và ingest tài liệu theo thứ tự.

`docker compose down -v` xóa cả Docker volumes, bao gồm dữ liệu PostgreSQL/OpenFGA. Chỉ chạy nếu chắc chắn có thể mất dữ liệu.

## 15. Bảo mật và giới hạn

### Quy tắc bảo vệ hiện có

1. **Default deny/fail closed:** lỗi authorization hoặc thiếu metadata không cấp quyền ngầm.
2. **Lọc quyền trước khi đưa evidence vào LLM:** truy vấn SQL áp dụng unit, classification, trạng thái, approval và validity.
3. **Một context cho mỗi truy vấn:** không gộp membership của người kiêm nhiệm.
4. **LLM không quyết định quyền:** prompt chỉ nhận evidence đã được backend kiểm soát.
5. **Citation verification:** loại claim/citation không trỏ tới chunk evidence hợp lệ; quote được kiểm chứng theo cấu hình.
6. **Prompt-injection handling:** nội dung tài liệu được xem là evidence, không phải chỉ thị; câu hỏi/tài liệu không thể thay đổi quyền truy cập.
7. **Audit:** các thay đổi và kết quả xử lý được ghi vào `audit_log`; schema bảo vệ audit khỏi sửa/xóa thông thường.
8. **Password hashing:** mật khẩu tài khoản lưu dạng hash, bootstrap nhận mật khẩu qua prompt ẩn.

### Giới hạn chưa phải production

- Tài khoản được Admin tạo và đăng nhập bằng mật khẩu đã được xác minh; tuy nhiên, `X-Persona-Id` trên các request tiếp theo không phải session/token đã xác thực và có thể bị giả mạo. Cần thay bằng session an toàn hoặc token/OIDC có xác minh trước triển khai production.
- `/api/v1/personas` trả danh sách tài khoản/membership active; endpoint này tiết lộ danh sách identity và cần được giới hạn/bảo vệ phù hợp trước triển khai production.
- Mặc định OpenFGA Playground được bật trong Compose local; không expose cấu hình này ra Internet.
- Gemini client gửi prompt/evidence lên dịch vụ cloud nếu có API key. Cần xác định chính sách dữ liệu, phân loại nào được phép gửi, logging, retention và điều khoản nhà cung cấp trước khi dùng dữ liệu thật.
- Cần bổ sung cấu hình production như TLS, secret management, network policy, rate limiting, backup/restore, monitoring, retention, access review và quản lý migration.
- Bộ benchmark kiểm tra các trường hợp đã mô tả nhưng không chứng minh tuyệt đối không có mọi dạng leakage.

---

## Tài liệu liên quan

- [Hướng dẫn chạy nhanh](./RUN.md)
- [Schema PostgreSQL](./backend/db/schema.sql)
- [OpenFGA model](./backend/app/authz/model.fga)
- [Ví dụ dữ liệu tổ chức (tham khảo, không phải runtime source)](./data/org.yaml)
