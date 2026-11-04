CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS btree_gist;

-- 1. USERS
CREATE TABLE IF NOT EXISTS users (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_code VARCHAR(100) UNIQUE,
    display_name VARCHAR(150) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash TEXT,
    description TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'disabled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_lower
ON users (LOWER(email));

-- 2. ORGANIZATION UNITS
CREATE TABLE IF NOT EXISTS organization_units (
    unit_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(150) NOT NULL,
    unit_code VARCHAR(50) NOT NULL UNIQUE,
    unit_type VARCHAR(30) NOT NULL CHECK (unit_type IN ('group', 'company', 'division', 'department', 'team', 'project', 'committee')),
    parent_unit_id UUID REFERENCES organization_units(unit_id) ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ( parent_unit_id IS NULL OR parent_unit_id <> unit_id)
);

-- 3. MEMBERSHIPS
CREATE TABLE IF NOT EXISTS memberships (
    membership_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL
        REFERENCES users(user_id) ON DELETE RESTRICT,
    unit_id UUID NOT NULL
        REFERENCES organization_units(unit_id) ON DELETE RESTRICT,
    context_id VARCHAR(150) NOT NULL UNIQUE,
    membership_type VARCHAR(20) NOT NULL
        CHECK (membership_type IN (
            'primary',
            'matrix',
            'project'
        )),
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'inactive')),
    valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to TIMESTAMPTZ,
    job_title VARCHAR(150) NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

-- 4. ROLES
CREATE TABLE IF NOT EXISTS roles (
    role_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    role_code VARCHAR(50) NOT NULL UNIQUE,
    role_name VARCHAR(100) NOT NULL,
    max_classification SMALLINT NOT NULL DEFAULT 1
        CHECK (max_classification BETWEEN 0 AND 3),
    description TEXT
);

INSERT INTO roles (role_code, role_name, max_classification)
VALUES
    ('employee', 'Employee', 1),
    ('manager', 'Manager', 2),
    ('hr_owner', 'HR Owner', 2),
    ('security', 'Security', 3)
ON CONFLICT (role_code) DO NOTHING;

-- 5. ROLE ASSIGNMENTS
CREATE TABLE IF NOT EXISTS role_assignments (
    assignment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    membership_id UUID NOT NULL
        REFERENCES memberships(membership_id)
        ON DELETE RESTRICT,
    role_id BIGINT NOT NULL
        REFERENCES roles(role_id)
        ON DELETE RESTRICT,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'revoked', 'expired')),
    valid_from TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    UNIQUE (membership_id, role_id)
);

-- 6. DOCUMENTS
CREATE TABLE IF NOT EXISTS documents (
    document_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_code VARCHAR(100) NOT NULL UNIQUE,
    created_by UUID
        REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 7. DOCUMENT VERSIONS
CREATE TABLE IF NOT EXISTS document_versions (
    version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL REFERENCES documents(document_id) ON DELETE RESTRICT,
    version_label VARCHAR(30) NOT NULL,
    title VARCHAR(255) NOT NULL,
    doc_type VARCHAR(50),
    owner_unit_id UUID NOT NULL REFERENCES organization_units(unit_id) ON DELETE RESTRICT,
    scope VARCHAR(30) NOT NULL DEFAULT 'unit',
    classification SMALLINT NOT NULL DEFAULT 1 CHECK (classification BETWEEN 0 AND 3),
    scope_type VARCHAR(30) NOT NULL DEFAULT 'unit' CHECK (scope_type IN ('unit', 'subtree', 'organization','custom')),
    status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'processing', 'published', 'superseded', 'expired', 'rejected', 'failed')),
    valid_from TIMESTAMPTZ NOT NULL,
    valid_to TIMESTAMPTZ,
    mandatory BOOLEAN NOT NULL DEFAULT false,
    topic VARCHAR(100),
    parent_conformance VARCHAR(20) NOT NULL DEFAULT 'unverified' CHECK (parent_conformance IN ('confirmed', 'unverified', 'conflicting')),
    policy_values JSONB,
    approved BOOLEAN NOT NULL DEFAULT false,
    source_path TEXT,
    source_object_key TEXT NOT NULL,
    content_hash VARCHAR(128) NOT NULL,
    approved_by UUID REFERENCES users(user_id) ON DELETE SET NULL,
    approved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, version_label),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (status <> 'published' OR approved)
);

-- 8. DOCUMENT VERSION RELATIONS
CREATE TABLE IF NOT EXISTS document_version_relations (
    relation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_version_id UUID NOT NULL REFERENCES document_versions(version_id) ON DELETE RESTRICT,
    target_version_id UUID NOT NULL REFERENCES document_versions(version_id) ON DELETE RESTRICT,
    relation_type VARCHAR(30) NOT NULL
        CHECK (relation_type IN ('supersedes', 'amends', 'supplements','conflicts_with' )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE ( source_version_id, target_version_id, relation_type),
    CHECK (source_version_id <> target_version_id)
);

-- 9. CHUNKS
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL
        REFERENCES document_versions(version_id)
        ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    section_path TEXT,
    content TEXT NOT NULL,
    token_count INTEGER,
    embedding VECTOR(1024),
    content_tsv TSVECTOR,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (version_id, chunk_index),
    CHECK (chunk_index >= 0),
    CHECK (token_count IS NULL OR token_count >= 0)
);

-- Automatically update full-text search vector
CREATE OR REPLACE FUNCTION update_chunk_tsv()
RETURNS TRIGGER AS $$
BEGIN
    NEW.content_tsv :=
        to_tsvector(
            'simple',
            unaccent(
                COALESCE(NEW.section_path, '') || ' ' ||
                COALESCE(NEW.content, '')
            )
        );

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_update_chunk_tsv ON chunks;
CREATE TRIGGER trg_update_chunk_tsv
BEFORE INSERT OR UPDATE OF section_path, content
ON chunks
FOR EACH ROW
EXECUTE FUNCTION update_chunk_tsv();

-- 10. CONVERSATIONS
CREATE TABLE IF NOT EXISTS conversations (
    conversation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL
        REFERENCES users(user_id)
        ON DELETE RESTRICT,
    title VARCHAR(255),
    context_id VARCHAR(150) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 11. MESSAGES
CREATE TABLE IF NOT EXISTS messages (
    message_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL
        REFERENCES conversations(conversation_id)
        ON DELETE CASCADE,
    role VARCHAR(20) NOT NULL
        CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    token_count INTEGER,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (token_count IS NULL OR token_count >= 0)
);

-- 12. MESSAGE SOURCES
CREATE TABLE IF NOT EXISTS message_sources (
    source_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id UUID NOT NULL
        REFERENCES messages(message_id)
        ON DELETE CASCADE,
    chunk_id UUID NOT NULL
        REFERENCES chunks(chunk_id)
        ON DELETE RESTRICT,
    source_order INTEGER NOT NULL,
    retrieval_score REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (message_id, chunk_id),
    UNIQUE (message_id, source_order)
);

-- 13. AUDIT LOG
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id UUID,
    actor_user_id UUID
        REFERENCES users(user_id)
        ON DELETE SET NULL,
    event_type VARCHAR(50) NOT NULL,
    resource_type VARCHAR(50),
    resource_id TEXT,
    decision VARCHAR(20)
        CHECK ( decision IS NULL OR decision IN ('allow', 'deny', 'success', 'failure')),
    reason_code VARCHAR(100),
    policy_version VARCHAR(50),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 14. INGESTION JOBS
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    job_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_id UUID NOT NULL
        REFERENCES document_versions(version_id)
        ON DELETE RESTRICT,
    job_type VARCHAR(30) NOT NULL
        CHECK (job_type IN (
            'full_ingestion',
            'reindex',
            'reembed'
        )),
    status VARCHAR(20) NOT NULL DEFAULT 'queued'
        CHECK (status IN (
            'queued',
            'processing',
            'succeeded',
            'failed',
            'cancelled'
        )),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    parser_name VARCHAR(100),
    parser_version VARCHAR(50),
    embedding_model VARCHAR(100),
    error_message TEXT,
    created_by UUID
        REFERENCES users(user_id)
        ON DELETE SET NULL,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (attempts >= 0),
    CHECK (max_attempts > 0)
);

-- 15. AUTHORIZATION OUTBOX
CREATE TABLE IF NOT EXISTS authz_outbox (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key VARCHAR(150) NOT NULL UNIQUE,
    event_type VARCHAR(50) NOT NULL,
    tuple_payload JSONB NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN (
            'pending',
            'processing',
            'completed',
            'failed'
        )),
    retry_count INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ,
    CHECK (retry_count >= 0)
);

-- 16. INDEXES
CREATE INDEX IF NOT EXISTS idx_memberships_user
ON memberships(user_id);

CREATE INDEX IF NOT EXISTS idx_memberships_unit
ON memberships(unit_id);

CREATE INDEX IF NOT EXISTS idx_memberships_active
ON memberships(user_id, unit_id)
WHERE status = 'active';

-- Role assignments
CREATE INDEX IF NOT EXISTS idx_role_assignments_membership
ON role_assignments(membership_id);

CREATE INDEX IF NOT EXISTS idx_role_assignments_active
ON role_assignments(membership_id, role_id)
WHERE status = 'active';

-- Organization hierarchy
CREATE INDEX IF NOT EXISTS idx_org_units_parent
ON organization_units(parent_unit_id);

CREATE INDEX IF NOT EXISTS idx_documents_versions
ON document_versions(document_id);

CREATE INDEX IF NOT EXISTS idx_versions_owner
ON document_versions(owner_unit_id);

CREATE INDEX IF NOT EXISTS idx_versions_classification
ON document_versions(classification);

CREATE INDEX IF NOT EXISTS idx_versions_status_validity
ON document_versions(status, valid_from, valid_to);

CREATE INDEX IF NOT EXISTS idx_versions_topic
ON document_versions(topic)
WHERE topic IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_version_relations_source
ON document_version_relations(source_version_id);

CREATE INDEX IF NOT EXISTS idx_version_relations_target
ON document_version_relations(target_version_id);

-- Chunk lookup by document version
CREATE INDEX IF NOT EXISTS idx_chunks_version
ON chunks(version_id);

-- Full-text search
CREATE INDEX IF NOT EXISTS idx_chunks_content_tsv
ON chunks USING GIN(content_tsv);

-- Semantic search
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
ON chunks
USING HNSW (embedding vector_cosine_ops)
WITH (
    m = 16,
    ef_construction = 64
);

CREATE INDEX IF NOT EXISTS idx_conversations_user
ON conversations(user_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
ON messages(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_message_sources_message
ON message_sources(message_id);

CREATE INDEX IF NOT EXISTS idx_message_sources_chunk
ON message_sources(chunk_id);

CREATE INDEX IF NOT EXISTS idx_audit_actor_time
ON audit_log(actor_user_id, occurred_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_request
ON audit_log(request_id);

CREATE INDEX IF NOT EXISTS idx_audit_resource
ON audit_log(resource_type, resource_id);

CREATE INDEX IF NOT EXISTS idx_ingestion_status
ON ingestion_jobs(status, created_at);

CREATE INDEX IF NOT EXISTS idx_ingestion_version
ON ingestion_jobs(version_id);

CREATE INDEX IF NOT EXISTS idx_outbox_pending
ON authz_outbox(available_at, created_at)
WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_outbox_processing
ON authz_outbox(created_at)
WHERE status = 'processing';

-- Audit log không được sửa hoặc xóa tùy tiện
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_immutable ON audit_log;
CREATE TRIGGER trg_audit_immutable
BEFORE UPDATE OR DELETE
ON audit_log
FOR EACH ROW
EXECUTE FUNCTION prevent_audit_modification();

-- Không cho phép hai phiên bản published của cùng tài liệu có thời gian hiệu lực chồng lấn
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'ex_document_version_validity'
          AND conrelid = 'document_versions'::regclass
    ) THEN
        ALTER TABLE document_versions
        ADD CONSTRAINT ex_document_version_validity
        EXCLUDE USING gist (
            document_id WITH =,
            tstzrange(valid_from, valid_to, '[)') WITH &&
        )
        WHERE (status = 'published');
    END IF;
END $$;
