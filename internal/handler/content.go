package handler

import (
	"encoding/json"
	"net/http"

	"github.com/go-chi/chi/v5"
	"github.com/pkn/api/internal/model"
	"github.com/pkn/api/internal/service"
)

type ContentHandler struct{}

func NewContentHandler() *ContentHandler {
	return &ContentHandler{}
}

func (h *ContentHandler) GetSection(w http.ResponseWriter, r *http.Request) {
	app := chi.URLParam(r, "app")
	section := chi.URLParam(r, "section")

	content, err := service.GetContent(app, section)
	if err != nil {
		if err == service.ErrContentNotFound {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "not found"})
		} else {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		}
		return
	}
	writeJSON(w, http.StatusOK, content)
}

func (h *ContentHandler) UpsertSection(w http.ResponseWriter, r *http.Request) {
	app := chi.URLParam(r, "app")
	section := chi.URLParam(r, "section")

	var body struct {
		Data json.RawMessage `json:"data"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}
	if !json.Valid(body.Data) {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON data"})
		return
	}

	if err := service.UpsertContent(app, section, body.Data); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"message": "updated"})
}

func (h *ContentHandler) ListByApp(w http.ResponseWriter, r *http.Request) {
	app := chi.URLParam(r, "app")

	contents, err := service.ListContentByApp(app)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}
	if contents == nil {
		contents = []model.SiteContent{}
	}
	writeJSON(w, http.StatusOK, contents)
}
