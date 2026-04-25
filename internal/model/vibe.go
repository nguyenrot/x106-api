package model

import (
	"fmt"
	"time"
)

type DateOnly string

func (d *DateOnly) Scan(value interface{}) error {
	switch v := value.(type) {
	case time.Time:
		*d = DateOnly(v.Format("2006-01-02"))
	case string:
		*d = DateOnly(v)
	case []byte:
		*d = DateOnly(string(v))
	case nil:
		*d = ""
	default:
		return fmt.Errorf("cannot scan %T into DateOnly", value)
	}
	return nil
}

type NullDateOnly struct {
	Date  DateOnly
	Valid bool
}

func (d *NullDateOnly) Scan(value interface{}) error {
	if value == nil {
		d.Valid = false
		return nil
	}
	d.Valid = true
	return d.Date.Scan(value)
}

type Vibe struct {
	ID        string         `json:"id"`
	UserID    string         `json:"user_id"`
	Date      DateOnly       `json:"date"`
	MoodEmoji string         `json:"mood_emoji"`
	Note      *string        `json:"note,omitempty"`
	CreatedAt time.Time      `json:"created_at"`
}

type UpsertVibeRequest struct {
	Date      string  `json:"date"`
	MoodEmoji string  `json:"mood_emoji"`
	Note      *string `json:"note,omitempty"`
}

type VibeStats struct {
	TotalEntries int            `json:"total_entries"`
	Streak       int            `json:"streak"`
	MoodCounts   map[string]int `json:"mood_counts"`
}
