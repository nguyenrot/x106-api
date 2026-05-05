package model

// Shapes used by the admin "art" management endpoints.

type ArtUserRow struct {
	UserID      string `json:"userId"`
	Username    string `json:"username"`
	DisplayName string `json:"displayName,omitempty"`
	UsedToday   int    `json:"usedToday"`
	Remaining   int    `json:"remaining"`
	UsedTotal   int    `json:"usedTotal"`
	Artworks    int    `json:"artworks"`
}

type ArtUsersResponse struct {
	Users []ArtUserRow `json:"users"`
	Limit int          `json:"limit"`
	Date  string       `json:"date"`
}

type ArtSetQuotaRequest struct {
	Count int `json:"count"`
}

type ArtAdjustQuotaRequest struct {
	Delta int `json:"delta"`
}

type ArtStatsResponse struct {
	Date          string `json:"date"`
	TotalToday    int    `json:"totalToday"`
	UsersTodayHit int    `json:"usersTodayHit"`
	Users7d       int    `json:"usersActive7d"`
	Total7d       int    `json:"total7d"`
	Limit         int    `json:"limit"`
	Enabled       bool   `json:"enabled"`
	Configured    bool   `json:"configured"`
}

type ArtPromptResponse struct {
	Prompt    string `json:"prompt"`
	IsDefault bool   `json:"isDefault"`
	Default   string `json:"default"`
}

type ArtPromptUpdateRequest struct {
	Prompt string `json:"prompt"`
}

type ArtSettingsResponse struct {
	DailyLimit int  `json:"dailyLimit"`
	Enabled    bool `json:"enabled"`
	Configured bool `json:"configured"`
	Model      string `json:"model"`
	BaseURL    string `json:"baseUrl"`
}

type ArtSettingsUpdateRequest struct {
	DailyLimit *int  `json:"dailyLimit,omitempty"`
	Enabled    *bool `json:"enabled,omitempty"`
}
