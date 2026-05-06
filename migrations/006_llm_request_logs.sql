-- Local dev DB; on VPS the database is `finance_app`.
-- Production runs this implicitly via internal/database/schema.go ensureLLMRequestLogsTable()
-- on every API startup, so no manual `docker exec` is required for this table.
USE x106;

CREATE TABLE IF NOT EXISTS llm_request_logs (
    id                BIGINT       NOT NULL AUTO_INCREMENT,
    user_id           VARCHAR(36)  NOT NULL,
    username          VARCHAR(64)  NOT NULL DEFAULT '',
    mode              VARCHAR(16)  NOT NULL,
    model             VARCHAR(64)  NOT NULL,
    attempt           TINYINT      NOT NULL DEFAULT 1,
    temperature       DECIMAL(3,2) NOT NULL DEFAULT 0,
    request_payload   JSON         NULL,
    response_raw      MEDIUMTEXT   NULL,
    parsed_direction  JSON         NULL,
    status            VARCHAR(24)  NOT NULL,
    error_message     TEXT         NULL,
    latency_ms        INT          NOT NULL DEFAULT 0,
    prompt_tokens     INT          NOT NULL DEFAULT 0,
    completion_tokens INT          NOT NULL DEFAULT 0,
    total_tokens      INT          NOT NULL DEFAULT 0,
    created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_llm_logs_created (created_at),
    KEY idx_llm_logs_user (user_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
