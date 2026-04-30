package service

import (
	"database/sql"
	"encoding/json"
	"errors"

	"github.com/pkn/api/internal/database"
	"github.com/pkn/api/internal/model"
)

var ErrArtworkNotFound = errors.New("artwork not found")

func ListArtworks(userID string) ([]model.Artwork, error) {
	rows, err := database.DB.Query(
		`SELECT id, user_id, kind, source_id, title, prompt, style, palette, seed, settings_json, scene_json, thumbnail_data_url, asset_data_url, created_at, updated_at
		 FROM artworks WHERE user_id = ? ORDER BY created_at DESC LIMIT 60`,
		userID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var artworks []model.Artwork
	for rows.Next() {
		artwork, err := scanArtwork(rows)
		if err != nil {
			return nil, err
		}
		artworks = append(artworks, artwork)
	}
	return artworks, rows.Err()
}

func GetArtwork(userID string, artworkID string) (*model.Artwork, error) {
	row := database.DB.QueryRow(
		`SELECT id, user_id, kind, source_id, title, prompt, style, palette, seed, settings_json, scene_json, thumbnail_data_url, asset_data_url, created_at, updated_at
		 FROM artworks WHERE user_id = ? AND id = ? LIMIT 1`,
		userID, artworkID,
	)

	artwork, err := scanArtwork(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrArtworkNotFound
	}
	if err != nil {
		return nil, err
	}
	return &artwork, nil
}

func CreateArtwork(userID string, req model.CreateArtworkRequest) (*model.Artwork, error) {
	id := newID()
	settings := req.Settings
	if len(settings) == 0 {
		settings = json.RawMessage(`{}`)
	}
	scene := req.Scene
	if len(scene) == 0 {
		scene = json.RawMessage(`{}`)
	}

	_, err := database.DB.Exec(
		`INSERT INTO artworks (id, user_id, kind, source_id, title, prompt, style, palette, seed, settings_json, scene_json, thumbnail_data_url, asset_data_url)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		id,
		userID,
		req.Kind,
		req.SourceID,
		req.Title,
		req.Prompt,
		req.Style,
		req.Palette,
		req.Seed,
		string(settings),
		string(scene),
		req.ThumbnailDataURL,
		req.AssetDataURL,
	)
	if err != nil {
		return nil, err
	}

	return GetArtwork(userID, id)
}

func DeleteArtwork(userID string, artworkID string) error {
	result, err := database.DB.Exec(
		`DELETE FROM artworks WHERE user_id = ? AND id = ?`,
		userID, artworkID,
	)
	if err != nil {
		return err
	}

	affected, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if affected == 0 {
		return ErrArtworkNotFound
	}
	return nil
}

type artworkScanner interface {
	Scan(dest ...any) error
}

func scanArtwork(scanner artworkScanner) (model.Artwork, error) {
	var artwork model.Artwork
	var settings []byte
	var scene []byte

	err := scanner.Scan(
		&artwork.ID,
		&artwork.UserID,
		&artwork.Kind,
		&artwork.SourceID,
		&artwork.Title,
		&artwork.Prompt,
		&artwork.Style,
		&artwork.Palette,
		&artwork.Seed,
		&settings,
		&scene,
		&artwork.ThumbnailDataURL,
		&artwork.AssetDataURL,
		&artwork.CreatedAt,
		&artwork.UpdatedAt,
	)
	if err != nil {
		return artwork, err
	}

	if len(settings) == 0 {
		settings = []byte(`{}`)
	}
	if len(scene) == 0 {
		scene = []byte(`{}`)
	}
	if artwork.Kind == "" {
		artwork.Kind = "snapshot"
	}
	artwork.Settings = json.RawMessage(settings)
	artwork.Scene = json.RawMessage(scene)

	return artwork, nil
}
