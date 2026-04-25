package service

import (
	"database/sql"
	"errors"
	"time"

	"github.com/pkn/api/internal/database"
	"github.com/pkn/api/internal/model"
)

var ErrVibeNotFound = errors.New("vibe not found")

func ListVibes(userID string) ([]model.Vibe, error) {
	rows, err := database.DB.Query(
		`SELECT id, user_id, date, mood_emoji, note, created_at
		 FROM vibes WHERE user_id = ? ORDER BY date DESC`,
		userID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var vibes []model.Vibe
	for rows.Next() {
		var v model.Vibe
		if err := rows.Scan(&v.ID, &v.UserID, &v.Date, &v.MoodEmoji, &v.Note, &v.CreatedAt); err != nil {
			return nil, err
		}
		vibes = append(vibes, v)
	}
	return vibes, rows.Err()
}

func GetTodayVibe(userID string) (*model.Vibe, error) {
	today := todayLocal()
	v := &model.Vibe{}
	err := database.DB.QueryRow(
		`SELECT id, user_id, date, mood_emoji, note, created_at
		 FROM vibes WHERE user_id = ? AND date = ? LIMIT 1`,
		userID, today,
	).Scan(&v.ID, &v.UserID, &v.Date, &v.MoodEmoji, &v.Note, &v.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return v, nil
}

func UpsertVibe(userID string, req model.UpsertVibeRequest) error {
	date := req.Date
	if date == "" {
		date = todayLocal()
	}
	_, err := database.DB.Exec(
		`INSERT INTO vibes (user_id, date, mood_emoji, note)
		 VALUES (?, ?, ?, ?)
		 ON DUPLICATE KEY UPDATE mood_emoji = VALUES(mood_emoji), note = VALUES(note)`,
		userID, date, req.MoodEmoji, req.Note,
	)
	return err
}

func GetVibeStats(userID string) (*model.VibeStats, error) {
	vibes, err := ListVibes(userID)
	if err != nil {
		return nil, err
	}

	stats := &model.VibeStats{
		TotalEntries: len(vibes),
		Streak:       computeStreak(vibes),
		MoodCounts:   make(map[string]int),
	}
	for _, v := range vibes {
		stats.MoodCounts[v.MoodEmoji]++
	}
	return stats, nil
}

func todayLocal() string {
	loc, _ := time.LoadLocation("Asia/Ho_Chi_Minh")
	return time.Now().In(loc).Format("2006-01-02")
}

func computeStreak(vibes []model.Vibe) int {
	if len(vibes) == 0 {
		return 0
	}

	dateSet := make(map[string]bool)
	for _, v := range vibes {
		dateSet[string(v.Date)] = true
	}

	loc, _ := time.LoadLocation("Asia/Ho_Chi_Minh")
	cursor := time.Now().In(loc)
	streak := 0

	for {
		dateStr := cursor.Format("2006-01-02")
		if dateSet[dateStr] {
			streak++
			cursor = cursor.AddDate(0, 0, -1)
		} else {
			break
		}
	}
	return streak
}
