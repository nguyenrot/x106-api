package model

import (
	"encoding/json"
	"time"
)

type SiteContent struct {
	App       string          `json:"app"`
	Section   string          `json:"section"`
	Data      json.RawMessage `json:"data"`
	UpdatedAt time.Time       `json:"updated_at"`
}
