-- X106 Unified Schema
-- Run: mysql -u root -p < migrations/001_init.sql

CREATE DATABASE IF NOT EXISTS x106
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE x106;

-- ═══════════════════════════════════════════
-- Users (shared across all X106 apps)
-- ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS users (
    id            VARCHAR(36)  NOT NULL DEFAULT (UUID()),
    username      VARCHAR(50)  NOT NULL,
    email         VARCHAR(255),
    password_hash VARCHAR(255) NOT NULL,
    display_name  VARCHAR(100),
    avatar_url    VARCHAR(500),
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_users_username (username)
) ENGINE=InnoDB;

-- ═══════════════════════════════════════════
-- Vibes (journal app)
-- ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS vibes (
    id          VARCHAR(36)  NOT NULL DEFAULT (UUID()),
    user_id     VARCHAR(36)  NOT NULL,
    date        DATE         NOT NULL,
    mood_emoji  VARCHAR(10)  NOT NULL,
    note        TEXT,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_vibes_user_date (user_id, date),
    CONSTRAINT fk_vibes_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- ═══════════════════════════════════════════
-- Sessions (optional — for future use)
-- ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS sessions (
    id         VARCHAR(36)  NOT NULL DEFAULT (UUID()),
    user_id    VARCHAR(36)  NOT NULL,
    token      VARCHAR(500) NOT NULL,
    expires_at DATETIME     NOT NULL,
    created_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_sessions_user (user_id),
    KEY idx_sessions_token (token(100)),
    CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB;
