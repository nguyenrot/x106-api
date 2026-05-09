package service

import (
	"database/sql"
	"errors"

	"github.com/pkn/api/internal/config"
	"github.com/pkn/api/internal/database"
)

const (
	SettingLLMSystemPrompt = "llm.system_prompt"
	SettingLLMDailyLimit   = "llm.daily_limit"
	SettingLLMEnabled      = "llm.enabled"
	SettingLLMModel        = "llm.model"
)

// AllowedLLMModels is the whitelist of DeepSeek model identifiers the admin
// UI may switch between. Order matches the dropdown order. v4-pro là default
// — quality cao hơn flash đáng kể cho compositional layout (flash dồn shape
// vào vùng nhỏ, không spread bbox). Streaming + 60s idle timeout giúp v4-pro
// không bị hard timeout khi reasoning chain im lặng vài chục giây đầu.
var AllowedLLMModels = []string{
	"deepseek-v4-pro",
	"deepseek-v4-flash",
}

func IsAllowedLLMModel(m string) bool {
	for _, x := range AllowedLLMModels {
		if x == m {
			return true
		}
	}
	return false
}

// EffectiveModel resolves which DeepSeek model the runtime should use:
//  1. DB setting llm.model (set via admin UI) — takes priority so swapping
//     models doesn't require a restart.
//  2. Env var DEEPSEEK_MODEL via cfg.DeepSeekModel.
//  3. Hardcoded default in cfg (currently deepseek-v4-pro).
//
// Returns the env/default when the DB value isn't on the whitelist so an
// invalid stored value can never break the runtime.
func EffectiveModel(cfg *config.Config) string {
	v, _ := GetSetting(SettingLLMModel)
	if v != "" && IsAllowedLLMModel(v) {
		return v
	}
	if cfg != nil && cfg.DeepSeekModel != "" {
		return cfg.DeepSeekModel
	}
	return AllowedLLMModels[0]
}

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
