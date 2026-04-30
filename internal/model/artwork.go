package model

import (
	"encoding/json"
	"time"
)

type Artwork struct {
	ID               string          `json:"id"`
	UserID           string          `json:"user_id"`
	Kind             string          `json:"kind"`
	SourceID         *string         `json:"source_id,omitempty"`
	Title            string          `json:"title"`
	Prompt           string          `json:"prompt"`
	Style            string          `json:"style"`
	Palette          string          `json:"palette"`
	Seed             int64           `json:"seed"`
	Settings         json.RawMessage `json:"settings"`
	Scene            json.RawMessage `json:"scene,omitempty"`
	ThumbnailDataURL string          `json:"thumbnail_data_url"`
	AssetDataURL     *string         `json:"asset_data_url,omitempty"`
	CreatedAt        time.Time       `json:"created_at"`
	UpdatedAt        time.Time       `json:"updated_at"`
}

type CreateArtworkRequest struct {
	Kind             string          `json:"kind"`
	SourceID         *string         `json:"source_id,omitempty"`
	Title            string          `json:"title"`
	Prompt           string          `json:"prompt"`
	Style            string          `json:"style"`
	Palette          string          `json:"palette"`
	Seed             int64           `json:"seed"`
	Settings         json.RawMessage `json:"settings"`
	Scene            json.RawMessage `json:"scene,omitempty"`
	ThumbnailDataURL string          `json:"thumbnail_data_url"`
	AssetDataURL     *string         `json:"asset_data_url,omitempty"`
}
