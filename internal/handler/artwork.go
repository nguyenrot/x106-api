package handler

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/pkn/api/internal/model"
	"github.com/pkn/api/internal/service"
)

const (
	maxArtworkTitle     = 80
	maxArtworkPrompt    = 180
	maxArtworkStyle     = 40
	maxArtworkPalette   = 60
	maxArtworkKind      = 24
	maxArtworkSourceID  = 80
	maxArtworkSettings  = 4096
	maxArtworkScene     = 65536
	maxArtworkThumbnail = 520000
	maxArtworkAsset     = 900000
)

type ArtworkHandler struct{}

func NewArtworkHandler() *ArtworkHandler {
	return &ArtworkHandler{}
}

func (h *ArtworkHandler) ListArtworks(w http.ResponseWriter, r *http.Request) {
	userID := GetUserID(r)
	artworks, err := service.ListArtworks(userID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}
	if artworks == nil {
		artworks = []model.Artwork{}
	}
	writeJSON(w, http.StatusOK, artworks)
}

func (h *ArtworkHandler) GetArtwork(w http.ResponseWriter, r *http.Request) {
	userID := GetUserID(r)
	artworkID := chi.URLParam(r, "id")

	artwork, err := service.GetArtwork(userID, artworkID)
	if err != nil {
		if err == service.ErrArtworkNotFound {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "artwork not found"})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}

	writeJSON(w, http.StatusOK, artwork)
}

func (h *ArtworkHandler) CreateArtwork(w http.ResponseWriter, r *http.Request) {
	userID := GetUserID(r)
	var req model.CreateArtworkRequest
	decoder := json.NewDecoder(r.Body)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}

	req.Title = cleanText(req.Title, maxArtworkTitle)
	req.Prompt = cleanText(req.Prompt, maxArtworkPrompt)
	req.Style = cleanText(req.Style, maxArtworkStyle)
	req.Palette = cleanText(req.Palette, maxArtworkPalette)
	req.Kind = cleanText(req.Kind, maxArtworkKind)
	if req.SourceID != nil {
		sourceID := cleanText(*req.SourceID, maxArtworkSourceID)
		req.SourceID = &sourceID
	}

	if req.Prompt == "" {
		req.Prompt = "Digital artwork"
	}
	if req.Title == "" {
		req.Title = req.Prompt
	}
	if req.Style == "" {
		req.Style = "bold-digital-gallery"
	}
	if req.Palette == "" {
		req.Palette = "signal-red"
	}
	if req.Kind == "" {
		req.Kind = "snapshot"
	}
	if !validArtworkKind(req.Kind) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "kind must be favorite, upload, or snapshot"})
		return
	}

	if err := validateJSONObject(req.Settings, maxArtworkSettings, "settings"); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if err := validateJSONObject(req.Scene, maxArtworkScene, "scene"); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	if !validThumbnail(req.ThumbnailDataURL) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "thumbnail_data_url must be a small webp or jpeg data URL"})
		return
	}
	if req.AssetDataURL != nil {
		asset := strings.TrimSpace(*req.AssetDataURL)
		if asset == "" {
			req.AssetDataURL = nil
		} else {
			req.AssetDataURL = &asset
			if !validDataURL(asset, maxArtworkAsset) {
				writeJSON(w, http.StatusBadRequest, map[string]string{"error": "asset_data_url must be a small webp or jpeg data URL"})
				return
			}
		}
	}

	artwork, err := service.CreateArtwork(userID, req)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}

	writeJSON(w, http.StatusCreated, artwork)
}

func (h *ArtworkHandler) DeleteArtwork(w http.ResponseWriter, r *http.Request) {
	userID := GetUserID(r)
	artworkID := chi.URLParam(r, "id")

	if err := service.DeleteArtwork(userID, artworkID); err != nil {
		if err == service.ErrArtworkNotFound {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "artwork not found"})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"message": "artwork deleted"})
}

func cleanText(value string, max int) string {
	value = strings.TrimSpace(value)
	if len(value) <= max {
		return value
	}

	runes := []rune(value)
	if len(runes) <= max {
		return value
	}
	return string(runes[:max])
}

func validateJSONObject(value json.RawMessage, max int, label string) error {
	if len(value) == 0 {
		return nil
	}
	if len(value) > max {
		return httpError(label + " payload is too large")
	}
	if !json.Valid(value) {
		return httpError(label + " must be valid JSON")
	}

	trimmed := strings.TrimSpace(string(value))
	if !strings.HasPrefix(trimmed, "{") {
		return httpError(label + " must be a JSON object")
	}
	return nil
}

func validThumbnail(value string) bool {
	return validDataURL(value, maxArtworkThumbnail)
}

func validDataURL(value string, max int) bool {
	if len(value) == 0 || len(value) > max {
		return false
	}
	return strings.HasPrefix(value, "data:image/webp;base64,") ||
		strings.HasPrefix(value, "data:image/jpeg;base64,")
}

func validArtworkKind(kind string) bool {
	return kind == "favorite" || kind == "upload" || kind == "snapshot"
}

type httpError string

func (e httpError) Error() string {
	return string(e)
}
