package handler

import (
	"encoding/json"
	"net/http"

	"github.com/pkn/api/internal/model"
	"github.com/pkn/api/internal/service"
)

// JournalHandler handles journal.pkn.io.vn endpoints.
type JournalHandler struct{}

func NewJournalHandler() *JournalHandler {
	return &JournalHandler{}
}

func (h *JournalHandler) ListVibes(w http.ResponseWriter, r *http.Request) {
	userID := GetUserID(r)
	vibes, err := service.ListVibes(userID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}
	if vibes == nil {
		vibes = []model.Vibe{}
	}
	writeJSON(w, http.StatusOK, vibes)
}

func (h *JournalHandler) GetTodayVibe(w http.ResponseWriter, r *http.Request) {
	userID := GetUserID(r)
	vibe, err := service.GetTodayVibe(userID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}
	writeJSON(w, http.StatusOK, vibe)
}

func (h *JournalHandler) UpsertVibe(w http.ResponseWriter, r *http.Request) {
	userID := GetUserID(r)
	var req model.UpsertVibeRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}
	if req.MoodEmoji == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "mood_emoji is required"})
		return
	}
	if req.Title == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "title is required"})
		return
	}

	if err := service.UpsertVibe(userID, req); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}

	writeJSON(w, http.StatusOK, map[string]string{"message": "vibe saved"})
}

func (h *JournalHandler) Stats(w http.ResponseWriter, r *http.Request) {
	userID := GetUserID(r)
	stats, err := service.GetVibeStats(userID)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}
	writeJSON(w, http.StatusOK, stats)
}
