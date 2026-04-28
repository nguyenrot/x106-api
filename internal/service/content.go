package service

import (
	"database/sql"
	"encoding/json"
	"errors"

	"github.com/pkn/api/internal/database"
	"github.com/pkn/api/internal/model"
)

var ErrContentNotFound = errors.New("content not found")

func GetContent(app, section string) (*model.SiteContent, error) {
	c := &model.SiteContent{}
	var data []byte
	err := database.DB.QueryRow(
		`SELECT app, section, data, updated_at FROM site_content WHERE app = ? AND section = ?`,
		app, section,
	).Scan(&c.App, &c.Section, &data, &c.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrContentNotFound
	}
	if err != nil {
		return nil, err
	}
	c.Data = json.RawMessage(data)
	return c, nil
}

func UpsertContent(app, section string, data json.RawMessage) error {
	_, err := database.DB.Exec(
		`INSERT INTO site_content (app, section, data) VALUES (?, ?, ?)
		 ON DUPLICATE KEY UPDATE data = VALUES(data)`,
		app, section, []byte(data),
	)
	return err
}

func ListContentByApp(app string) ([]model.SiteContent, error) {
	rows, err := database.DB.Query(
		`SELECT app, section, data, updated_at FROM site_content WHERE app = ? ORDER BY section`,
		app,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var results []model.SiteContent
	for rows.Next() {
		var c model.SiteContent
		var data []byte
		if err := rows.Scan(&c.App, &c.Section, &data, &c.UpdatedAt); err != nil {
			return nil, err
		}
		c.Data = json.RawMessage(data)
		results = append(results, c)
	}
	return results, rows.Err()
}
