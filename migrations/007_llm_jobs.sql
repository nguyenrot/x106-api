-- Local dev DB; on VPS the database is `finance_app`.
-- Note: production runs this implicitly via internal/database/schema.go ensureLLMJobsTable()
-- on every API + worker startup, so no manual `docker exec` is required.
USE x106;

CREATE TABLE IF NOT EXISTS llm_jobs (
    id            CHAR(36)     NOT NULL,
    user_id       VARCHAR(36)  NOT NULL,
    username      VARCHAR(64)  NOT NULL DEFAULT '',
    mode          VARCHAR(16)  NOT NULL,
    status        VARCHAR(16)  NOT NULL DEFAULT 'pending',
    request_body  JSON         NULL,
    result_scene  JSON         NULL,
    error_message VARCHAR(500) NULL,
    attempt       INT          NOT NULL DEFAULT 0,
    created_at    DATETIME(3)  NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    started_at    DATETIME(3)  NULL,
    finished_at   DATETIME(3)  NULL,
    PRIMARY KEY (id),
    KEY idx_llm_jobs_status_created (status, created_at),
    KEY idx_llm_jobs_user_created (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
