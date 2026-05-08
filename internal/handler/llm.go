package handler

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"
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
	if mode != model.LLMModeRandom && req.CurrentScene == nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "currentScene is required for polish/remix"})
		return
	}

	// v4-pro reasoning chain often takes 25–40s; allow ~110s to cover one
	// retry without losing the whole attempt to ctx expiry between calls.
	ctx, cancel := context.WithTimeout(r.Context(), 110*time.Second)
	defer cancel()

	scene, used, remaining, err := service.GenerateLLMScene(ctx, h.cfg, userID, username, mode, req)
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
		Scene:     scene,
		Used:      used,
		Remaining: remaining,
		Limit:     service.EffectiveDailyLimit(h.cfg),
	})
}

// ─── Async job queue ─────────────────────────────────────────────────────
//
// Submit reserves quota and inserts a pending row, then returns immediately.
// The worker picks up the row from the queue, runs DeepSeek, and writes the
// result back. The client polls GetJob until status is terminal. CancelJob is
// best-effort: refunds quota only when the job hadn't started yet.

// Submit handles POST /api/v1/studio/llm/job.
func (h *LLMHandler) Submit(w http.ResponseWriter, r *http.Request) {
	userID := GetUserID(r)
	username := GetUsername(r)

	var body struct {
		Mode         model.LLMMode   `json:"mode"`
		CurrentScene *model.LLMScene `json:"currentScene,omitempty"`
		StrokeCount  int             `json:"strokeCount,omitempty"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body"})
		return
	}
	switch body.Mode {
	case model.LLMModeRandom, model.LLMModePolish, model.LLMModeRemix:
	default:
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid mode"})
		return
	}
	if body.Mode != model.LLMModeRandom && body.CurrentScene == nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "currentScene is required for polish/remix"})
		return
	}

	if h.cfg.DeepSeekAPIKey == "" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "AI mode not configured"})
		return
	}
	if !service.LLMEnabled() {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "AI mode disabled by admin"})
		return
	}

	limit := service.EffectiveDailyLimit(h.cfg)
	used, remaining, err := service.ReserveLLMQuota(userID, limit)
	if err != nil {
		if errors.Is(err, service.ErrQuotaExceeded) {
			writeJSON(w, http.StatusTooManyRequests, map[string]any{
				"error":     "quota exceeded",
				"limit":     limit,
				"remaining": 0,
			})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}

	jobID, err := service.CreateLLMJob(userID, username, body.Mode, model.LLMRequest{
		CurrentScene: body.CurrentScene,
		StrokeCount:  body.StrokeCount,
	})
	if err != nil {
		// Refund the slot we just reserved so the user isn't charged for our
		// internal failure.
		_ = service.RefundLLMQuota(userID)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "could not enqueue job"})
		return
	}

	writeJSON(w, http.StatusAccepted, model.LLMJobSubmitResponse{
		JobID:     jobID,
		Used:      used,
		Remaining: remaining,
		Limit:     limit,
	})
}

// GetJob handles GET /api/v1/studio/llm/job/{id}. Returns the current status
// plus quota counters; scene only when status=done.
func (h *LLMHandler) GetJob(w http.ResponseWriter, r *http.Request) {
	userID := GetUserID(r)
	jobID := chi.URLParam(r, "id")
	if jobID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing job id"})
		return
	}

	job, err := service.GetLLMJob(jobID, userID)
	if err != nil {
		if errors.Is(err, service.ErrJobNotFound) {
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "job not found"})
			return
		}
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}

	limit := service.EffectiveDailyLimit(h.cfg)
	used, remaining, err := service.GetQuota(userID, limit)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		return
	}

	resp := model.LLMJobStatusResponse{
		JobID:        job.ID,
		Status:       job.Status,
		Mode:         job.Mode,
		ErrorMessage: job.ErrorMessage,
		ElapsedMs:    elapsedMs(job),
		Used:         used,
		Remaining:    remaining,
		Limit:        limit,
	}
	if job.Status == model.LLMJobDone && job.ResultScene != nil {
		resp.Scene = job.ResultScene
	}
	writeJSON(w, http.StatusOK, resp)
}

// CancelJob handles POST /api/v1/studio/llm/job/{id}/cancel.
func (h *LLMHandler) CancelJob(w http.ResponseWriter, r *http.Request) {
	userID := GetUserID(r)
	jobID := chi.URLParam(r, "id")
	if jobID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing job id"})
		return
	}

	refunded, status, err := service.CancelLLMJob(jobID, userID)
	if err != nil {
		switch {
		case errors.Is(err, service.ErrJobNotFound):
			writeJSON(w, http.StatusNotFound, map[string]string{"error": "job not found"})
		case errors.Is(err, service.ErrJobInvalidStatus):
			// Already in a terminal state — treat as no-op success so the
			// client doesn't have to special-case "I tried to cancel a job
			// that already finished".
			writeJSON(w, http.StatusOK, map[string]any{
				"jobId":    jobID,
				"status":   status,
				"refunded": false,
				"noop":     true,
			})
		default:
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
		}
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"jobId":    jobID,
		"status":   status,
		"refunded": refunded,
	})
}

func elapsedMs(job *service.LLMJob) int {
	if job.StartedAt == nil {
		return 0
	}
	end := time.Now()
	if job.FinishedAt != nil {
		end = *job.FinishedAt
	}
	d := end.Sub(*job.StartedAt)
	if d < 0 {
		return 0
	}
	return int(d / time.Millisecond)
}
