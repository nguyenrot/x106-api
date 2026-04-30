package model

import (
	"encoding/json"
	"time"
)

type Artwork struct {
	ID               string          `json:"id"`
	UserID           string          `json:"user_id"`
	Title            string          `json:"title"`
	Prompt           string          `json:"prompt"`
	Style            string          `json:"style"`
	Palette          string          `json:"palette"`
	Seed             int64           `json:"seed"`
	Settings         json.RawMessage `json:"settings"`
	ThumbnailDataURL string          `json:"thumbnail_data_url"`
	CreatedAt        time.Time       `json:"created_at"`
	UpdatedAt        time.Time       `json:"updated_at"`
}

type CreateArtworkRequest struct {
	Title            string          `json:"title"`
	Prompt           string          `json:"prompt"`
	Style            string          `json:"style"`
	Palette          string          `json:"palette"`
	Seed             int64           `json:"seed"`
	Settings         json.RawMessage `json:"settings"`
	ThumbnailDataURL string          `json:"thumbnail_data_url"`
}
