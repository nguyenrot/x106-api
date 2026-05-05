package service

import (
	"database/sql"
	"errors"
	"fmt"

	"github.com/pkn/api/internal/database"
	"github.com/pkn/api/internal/model"
)

// ListArtUsers returns one row per user that has any artworks OR llm_usage,
// joined with their today's quota usage.
func ListArtUsers(limit int) ([]model.ArtUserRow, string, error) {
	today := todayLocal()
	q := `
		SELECT u.id,
		       u.username,
		       COALESCE(u.display_name, '') AS display_name,
		       COALESCE(today.count, 0)    AS used_today,
		       COALESCE(total.count, 0)    AS used_total,
		       COALESCE(art.count, 0)      AS artworks
		FROM users u
		LEFT JOIN (
			SELECT user_id, count FROM llm_usage WHERE date = ?
		) today ON today.user_id = u.id
		LEFT JOIN (
			SELECT user_id, SUM(count) AS count FROM llm_usage GROUP BY user_id
		) total ON total.user_id = u.id
		LEFT JOIN (
			SELECT user_id, COUNT(*) AS count FROM artworks GROUP BY user_id
		) art ON art.user_id = u.id
		WHERE today.user_id IS NOT NULL
		   OR total.user_id IS NOT NULL
		   OR art.user_id   IS NOT NULL
		ORDER BY used_today DESC, used_total DESC, u.username ASC
		LIMIT 500
	`
	rows, err := database.DB.Query(q, today)
	if err != nil {
		return nil, today, fmt.Errorf("list art users: %w", err)
	}
	defer rows.Close()

	out := make([]model.ArtUserRow, 0, 32)
	for rows.Next() {
		var r model.ArtUserRow
		if err := rows.Scan(&r.UserID, &r.Username, &r.DisplayName, &r.UsedToday, &r.UsedTotal, &r.Artworks); err != nil {
			return nil, today, err
		}
		r.Remaining = limit - r.UsedToday
		if r.Remaining < 0 {
			r.Remaining = 0
		}
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, today, err
	}
	return out, today, nil
}

// SetUserQuotaToday sets the user's count for today to an explicit value (clamped >= 0).
func SetUserQuotaToday(userID string, count int) (int, error) {
	if count < 0 {
		count = 0
	}
	today := todayLocal()
	if _, err := database.DB.Exec(
		`INSERT INTO llm_usage (user_id, date, count) VALUES (?, ?, ?)
		 ON DUPLICATE KEY UPDATE count = VALUES(count)`,
		userID, today, count,
	); err != nil {
		return 0, err
	}
	return count, nil
}

// AdjustUserQuotaToday applies a delta to today's count, clamped to [0, +inf).
func AdjustUserQuotaToday(userID string, delta int) (int, error) {
	today := todayLocal()
	var current int
	err := database.DB.QueryRow(
		`SELECT count FROM llm_usage WHERE user_id = ? AND date = ?`,
		userID, today,
	).Scan(&current)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return 0, err
	}
	next := current + delta
	if next < 0 {
		next = 0
	}
	return SetUserQuotaToday(userID, next)
}

// ResetUserQuotaToday removes today's row entirely (effectively reset to 0).
func ResetUserQuotaToday(userID string) error {
	today := todayLocal()
	_, err := database.DB.Exec(
		`DELETE FROM llm_usage WHERE user_id = ? AND date = ?`,
		userID, today,
	)
	return err
}

// ArtStats returns aggregate usage stats for the admin overview.
func ArtStats() (totalToday, usersTodayHit, total7d, users7d int, err error) {
	today := todayLocal()
	if err = database.DB.QueryRow(
		`SELECT COALESCE(SUM(count),0), COUNT(*) FROM llm_usage WHERE date = ?`,
		today,
	).Scan(&totalToday, &usersTodayHit); err != nil {
		return
	}
	if err = database.DB.QueryRow(
		`SELECT COALESCE(SUM(count),0), COUNT(DISTINCT user_id) FROM llm_usage
		 WHERE date >= DATE_SUB(?, INTERVAL 6 DAY)`,
		today,
	).Scan(&total7d, &users7d); err != nil {
		return
	}
	return
}
