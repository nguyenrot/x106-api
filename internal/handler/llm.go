package handler

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/pkn/api/internal/config"
	"github.com/pkn/api/internal/model"
	"github.com/pkn/api/internal/service"
)

type LLMHandler struct {
	cfg *config.Config
}

func NewLLMHandler(cfg *config.Config) *LLMHandler {
	return &LLMHandler{cfg: cfg}
}

func (h *LLMHandler) Quota(w http.ResponseWriter, r *http.Request) {
	userID := GetUserID(r)
	limit := service.EffectiveDailyLimit(h.cfg)
	used, remaining, err := service.GetQuota(userID, limit)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}
	writeJSON(w, http.StatusOK, model.LLMQuotaResponse{
		Used:      used,
		Remaining: remaining,
		Limit:     limit,
	})
}

func (h *LLMHandler) Random(w http.ResponseWriter, r *http.Request) { h.generate(w, r, model.LLMModeRandom) }
func (h *LLMHandler) Polish(w http.ResponseWriter, r *http.Request) { h.generate(w, r, model.LLMModePolish) }
func (h *LLMHandler) Remix(w http.ResponseWriter, r *http.Request)  { h.generate(w, r, model.LLMModeRemix) }

func (h *LLMHandler) generate(w http.ResponseWriter, r *http.Request, mode model.LLMMode) {
	userID := GetUserID(r)
	username := GetUsername(r)

	var req model.LLMRequest
	if r.ContentLength > 0 {
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
			return
		}
	}

	// Polish/remix should usually have a scene context.
	if mode != model.LLMModeRandom && req.Scene == nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "scene is required for polish/remix"})
		return
	}

	// v4-pro reasoning chain often takes 25–40s; allow ~110s to cover one
	// retry without losing the whole attempt to ctx expiry between calls.
	ctx, cancel := context.WithTimeout(r.Context(), 110*time.Second)
	defer cancel()

	dir, used, remaining, err := service.GenerateLLMDirection(ctx, h.cfg, userID, username, mode, req)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrQuotaExceeded):
			writeJSON(w, http.StatusTooManyRequests, map[string]any{
				"error":     "quota exceeded",
				"limit":     service.EffectiveDailyLimit(h.cfg),
				"remaining": 0,
			})
		case errors.Is(err, service.ErrLLMTimeout):
			writeJSON(w, http.StatusGatewayTimeout, map[string]string{"error": "LLM timeout"})
		case errors.Is(err, service.ErrLLMDisabled):
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "AI mode not configured"})
		case errors.Is(err, service.ErrLLMOff):
			writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "AI mode disabled by admin"})
		case errors.Is(err, service.ErrLLMUpstream):
			writeJSON(w, http.StatusBadGateway, map[string]string{"error": "LLM upstream error"})
		default:
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		}
		return
	}

	writeJSON(w, http.StatusOK, model.LLMResponse{
		Direction: dir,
		Used:      used,
		Remaining: remaining,
		Limit:     service.EffectiveDailyLimit(h.cfg),
	})
}
