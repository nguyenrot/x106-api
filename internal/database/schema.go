package database

import (
	"database/sql"
	"fmt"
	"strings"
)

func EnsureSchema() error {
	if err := ensureUsersTable(); err != nil {
		return err
	}
	if err := migrateJournalUsers(); err != nil {
		return err
	}
	if err := ensureArtworksTable(); err != nil {
		return err
	}
	if err := ensureLLMUsageTable(); err != nil {
		return err
	}
	if err := ensureAppSettingsTable(); err != nil {
		return err
	}
	if err := ensureLLMRequestLogsTable(); err != nil {
		return err
	}
	return nil
}

func ensureLLMRequestLogsTable() error {
	_, err := DB.Exec(`
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
		) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
	`)
	if err != nil {
		return fmt.Errorf("ensure llm_request_logs table: %w", err)
	}
	return nil
}

func ensureAppSettingsTable() error {
	_, err := DB.Exec(`
		CREATE TABLE IF NOT EXISTS app_settings (
			` + "`key`" + ` VARCHAR(80)  NOT NULL,
			value      MEDIUMTEXT  NOT NULL,
			updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
			PRIMARY KEY (` + "`key`" + `)
		) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
	`)
	if err != nil {
		return fmt.Errorf("ensure app_settings table: %w", err)
	}
	return nil
}

func ensureLLMUsageTable() error {
	// No FK to users — production users.id has incompatible charset/collation
	// (same reason artworks dropped its FK; see commit e4f3e2e). user_id always
	// comes from a verified JWT, so app-level integrity is sufficient.
	_, err := DB.Exec(`
		CREATE TABLE IF NOT EXISTS llm_usage (
			user_id    VARCHAR(36) NOT NULL,
			date       DATE        NOT NULL,
			count      INT         NOT NULL DEFAULT 0,
			updated_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
			PRIMARY KEY (user_id, date)
		) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
	`)
	if err != nil {
		return fmt.Errorf("ensure llm_usage table: %w", err)
	}
	return nil
}

func ensureUsersTable() error {
	_, err := DB.Exec(`
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
		) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
	`)
	if err != nil {
		return fmt.Errorf("ensure users table: %w", err)
	}

	return ensureUserColumns()
}

func ensureUserColumns() error {
	columns, err := tableColumns("users")
	if err != nil {
		return err
	}

	alterations := []struct {
		column string
		sql    string
	}{
		{"username", "ADD COLUMN username VARCHAR(50) AFTER id"},
		{"email", "ADD COLUMN email VARCHAR(255) AFTER username"},
		{"password_hash", "ADD COLUMN password_hash VARCHAR(255) AFTER email"},
		{"display_name", "ADD COLUMN display_name VARCHAR(100) AFTER password_hash"},
		{"avatar_url", "ADD COLUMN avatar_url VARCHAR(500) AFTER display_name"},
		{"created_at", "ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"},
		{"updated_at", "ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"},
	}

	for _, alteration := range alterations {
		if columns[alteration.column] {
			continue
		}
		if _, err := DB.Exec("ALTER TABLE users " + alteration.sql); err != nil {
			return fmt.Errorf("ensure users.%s: %w", alteration.column, err)
		}
	}
	return nil
}

func migrateJournalUsers() error {
	exists, err := tableExists("journal_users")
	if err != nil {
		return err
	}
	if !exists {
		return nil
	}

	columns, err := tableColumns("journal_users")
	if err != nil {
		return err
	}
	if !columns["username"] || !columns["password_hash"] {
		return nil
	}

	selects := []string{
		nullableColumnExpr(columns, "id", "UUID()"),
		"username",
		columnExpr(columns, "email", "NULL"),
		"password_hash",
		columnExpr(columns, "display_name", "NULL"),
		columnExpr(columns, "avatar_url", "NULL"),
		nullableColumnExpr(columns, "created_at", "CURRENT_TIMESTAMP"),
		nullableColumnExpr(columns, "updated_at", "CURRENT_TIMESTAMP"),
	}

	_, err = DB.Exec(`
		INSERT INTO users (id, username, email, password_hash, display_name, avatar_url, created_at, updated_at)
		SELECT ` + strings.Join(selects, ", ") + ` FROM journal_users
		ON DUPLICATE KEY UPDATE
			username = COALESCE(NULLIF(users.username, ''), VALUES(username)),
			email = COALESCE(users.email, VALUES(email)),
			password_hash = COALESCE(NULLIF(users.password_hash, ''), VALUES(password_hash)),
			display_name = COALESCE(users.display_name, VALUES(display_name)),
			avatar_url = COALESCE(users.avatar_url, VALUES(avatar_url))
	`)
	if err != nil {
		return fmt.Errorf("migrate journal_users to users: %w", err)
	}
	return nil
}

func ensureArtworksTable() error {
	_, err := DB.Exec(`
		CREATE TABLE IF NOT EXISTS artworks (
			id                 VARCHAR(36)  NOT NULL,
			user_id            VARCHAR(36)  NOT NULL,
			kind               VARCHAR(24)  NOT NULL DEFAULT 'snapshot',
			source_id          VARCHAR(80),
			title              VARCHAR(80)  NOT NULL,
			prompt             VARCHAR(180) NOT NULL,
			style              VARCHAR(40)  NOT NULL,
			palette            VARCHAR(60)  NOT NULL,
			seed               BIGINT       NOT NULL,
			settings_json      JSON         NOT NULL,
			scene_json         JSON         NOT NULL,
			thumbnail_data_url MEDIUMTEXT   NOT NULL,
			asset_data_url     MEDIUMTEXT,
			created_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
			updated_at         DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
			PRIMARY KEY (id),
			KEY idx_artworks_user_created (user_id, created_at)
		) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
	`)
	if err != nil {
		return fmt.Errorf("ensure artworks table: %w", err)
	}
	return ensureArtworkColumns()
}

func ensureArtworkColumns() error {
	columns, err := tableColumns("artworks")
	if err != nil {
		return err
	}

	alterations := []struct {
		column string
		sql    string
	}{
		{"kind", "ADD COLUMN kind VARCHAR(24) NOT NULL DEFAULT 'snapshot' AFTER user_id"},
		{"source_id", "ADD COLUMN source_id VARCHAR(80) AFTER kind"},
		{"scene_json", "ADD COLUMN scene_json JSON NOT NULL AFTER settings_json"},
		{"asset_data_url", "ADD COLUMN asset_data_url MEDIUMTEXT AFTER thumbnail_data_url"},
	}

	for _, alteration := range alterations {
		if columns[alteration.column] {
			continue
		}
		if _, err := DB.Exec("ALTER TABLE artworks " + alteration.sql); err != nil {
			return fmt.Errorf("ensure artworks.%s: %w", alteration.column, err)
		}
	}
	return nil
}

func tableExists(table string) (bool, error) {
	var name string
	err := DB.QueryRow(
		`SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? LIMIT 1`,
		table,
	).Scan(&name)
	if err == sql.ErrNoRows {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("check table %s: %w", table, err)
	}
	return true, nil
}

func tableColumns(table string) (map[string]bool, error) {
	rows, err := DB.Query(
		`SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?`,
		table,
	)
	if err != nil {
		return nil, fmt.Errorf("list columns %s: %w", table, err)
	}
	defer rows.Close()

	columns := make(map[string]bool)
	for rows.Next() {
		var column string
		if err := rows.Scan(&column); err != nil {
			return nil, err
		}
		columns[column] = true
	}
	return columns, rows.Err()
}

func columnExpr(columns map[string]bool, column string, fallback string) string {
	if columns[column] {
		return column
	}
	return fallback
}

func nullableColumnExpr(columns map[string]bool, column string, fallback string) string {
	if columns[column] {
		return "COALESCE(" + column + ", " + fallback + ")"
	}
	return fallback
}
