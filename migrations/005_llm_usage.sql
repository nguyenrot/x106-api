-- Local dev DB; on VPS the database is `finance_app`.
-- Note: production runs this implicitly via internal/database/schema.go ensureLLMUsageTable()
-- on every API startup, so no manual `docker exec` is required for this table.
USE x106;

CREATE TABLE IF NOT EXISTS llm_usage (
    user_id    VARCHAR(36) NOT NULL,
    date       DATE        NOT NULL,
    count      INT         NOT NULL DEFAULT 0,
    updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
