USE x106;

CREATE TABLE IF NOT EXISTS users (
    id            VARCHAR(36)  NOT NULL,
    username      VARCHAR(50)  NOT NULL,
    email         VARCHAR(255),
    password_hash VARCHAR(255) NOT NULL,
    display_name  VARCHAR(100),
    avatar_url    VARCHAR(500),
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS artworks (
    id                 VARCHAR(36)  NOT NULL,
    user_id            VARCHAR(36)  NOT NULL,
    title              VARCHAR(80)  NOT NULL,
    prompt             VARCHAR(180) NOT NULL,
    style              VARCHAR(40)  NOT NULL,
    palette            VARCHAR(60)  NOT NULL,
    seed               BIGINT       NOT NULL,
    settings_json      JSON         NOT NULL,
    thumbnail_data_url MEDIUMTEXT   NOT NULL,
    created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_artworks_user_created (user_id, created_at),
    CONSTRAINT fk_artworks_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Production safety note:
-- The API bootstrap also copies compatible rows from legacy journal_users into
-- users when journal_users exists. This keeps deploy safe even though the
-- current VPS deploy script does not run SQL migrations automatically.
