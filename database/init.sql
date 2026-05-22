-- ═══════════════════════════════════════════════════════════
-- PPTX-Slides Multi-Agent — Database Initialization
-- Runs automatically on first postgres startup
-- ═══════════════════════════════════════════════════════════

-- Create schemas (n8n uses its own schema, pptx_app for business data)
CREATE SCHEMA IF NOT EXISTS n8n;
CREATE SCHEMA IF NOT EXISTS pptx_app;

-- ═══ PPTX_APP SCHEMA ════════════════════════════════════════
SET search_path TO pptx_app;

-- Sessions: stores slide data per user session
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slides JSONB NOT NULL DEFAULT '[]',
    slide_history JSONB NOT NULL DEFAULT '[]',
    word_content TEXT DEFAULT '',
    document_topic TEXT DEFAULT '',
    theme VARCHAR(50),
    template_name VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Jobs: tracks each pipeline execution through agents
CREATE TABLE IF NOT EXISTS jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    status VARCHAR(20) DEFAULT 'pending',
    current_agent VARCHAR(20),
    progress_pct INT DEFAULT 0,
    progress_message TEXT DEFAULT '',
    error_message TEXT,
    -- Agent results stored as JSONB
    analyst_result JSONB,
    writer_result JSONB,
    designer_result JSONB,
    exporter_result JSONB,
    -- Config
    output_format VARCHAR(20) DEFAULT 'pptx',
    llm_provider VARCHAR(50),
    -- Timestamps
    created_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

-- Documents: metadata for uploaded files (files stored on shared volume)
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID REFERENCES sessions(id) ON DELETE CASCADE,
    filename VARCHAR(255),
    file_path VARCHAR(500),
    word_count INT,
    chunk_count INT DEFAULT 0,
    language VARCHAR(10) DEFAULT 'vi',
    was_chunked BOOLEAN DEFAULT FALSE,
    volume_path VARCHAR(500),
    uploaded_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_jobs_session ON jobs(session_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_documents_session ON documents(session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at);
