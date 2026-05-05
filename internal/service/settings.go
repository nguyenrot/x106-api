package service

import (
	"database/sql"
	"errors"

	"github.com/pkn/api/internal/database"
)

const (
	SettingLLMSystemPrompt = "llm.system_prompt"
	SettingLLMDailyLimit   = "llm.daily_limit"
	SettingLLMEnabled      = "llm.enabled"
)

func GetSetting(key string) (string, error) {
	var value string
	err := database.DB.QueryRow(
		"SELECT value FROM app_settings WHERE `key` = ?",
		key,
	).Scan(&value)
	if errors.Is(err, sql.ErrNoRows) {
		return "", nil
	}
	if err != nil {
		return "", err
	}
	return value, nil
}

func SetSetting(key, value string) error {
	_, err := database.DB.Exec(
		"INSERT INTO app_settings (`key`, value) VALUES (?, ?) ON DUPLICATE KEY UPDATE value = VALUES(value)",
		key, value,
	)
	return err
}

func DeleteSetting(key string) error {
	_, err := database.DB.Exec("DELETE FROM app_settings WHERE `key` = ?", key)
	return err
}
