package handler

import (
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/pkn/api/internal/config"
	"github.com/pkn/api/internal/model"
	"github.com/pkn/api/internal/service"
)

func itoa(n int) string         { return strconv.Itoa(n) }
func todayString() string       { return time.Now().Format("2006-01-02") }

type AdminArtHandler struct {
	cfg *config.Config
}

func NewAdminArtHandler(cfg *config.Config) *AdminArtHandler {
	return &AdminArtHandler{cfg: cfg}
}

// GET /api/v1/admin/art/users
func (h *AdminArtHandler) ListUsers(w http.ResponseWriter, r *http.Request) {
	limit := service.EffectiveDailyLimit(h.cfg)
	users, date, err := service.ListArtUsers(limit)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, model.ArtUsersResponse{
		Users: users,
		Limit: limit,
		Date:  date,
	})
}

// PUT /api/v1/admin/art/users/{id}/quota — body: {count}
func (h *AdminArtHandler) SetUserQuota(w http.ResponseWriter, r *http.Request) {
	userID := chi.URLParam(r, "id")
	if userID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "user id required"})
		return
	}
	var req model.ArtSetQuotaRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid body"})
		return
	}
	count, err := service.SetUserQuotaToday(userID, req.Count)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	limit := service.EffectiveDailyLimit(h.cfg)
	remaining := limit - count
	if remaining < 0 {
		remaining = 0
	}
	writeJSON(w, http.StatusOK, map[string]int{"count": count, "remaining": remaining, "limit": limit})
}

// POST /api/v1/admin/art/users/{id}/quota/adjust — body: {delta}
func (h *AdminArtHandler) AdjustUserQuota(w http.ResponseWriter, r *http.Request) {
	userID := chi.URLParam(r, "id")
	if userID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "user id required"})
		return
	}
	var req model.ArtAdjustQuotaRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid body"})
		return
	}
	count, err := service.AdjustUserQuotaToday(userID, req.Delta)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	limit := service.EffectiveDailyLimit(h.cfg)
	remaining := limit - count
	if remaining < 0 {
		remaining = 0
	}
	writeJSON(w, http.StatusOK, map[string]int{"count": count, "remaining": remaining, "limit": limit})
}

// DELETE /api/v1/admin/art/users/{id}/quota
func (h *AdminArtHandler) ResetUserQuota(w http.ResponseWriter, r *http.Request) {
	userID := chi.URLParam(r, "id")
	if userID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "user id required"})
		return
	}
	if err := service.ResetUserQuotaToday(userID); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"message": "reset"})
}

// GET /api/v1/admin/art/llm-prompt
func (h *AdminArtHandler) GetPrompt(w http.ResponseWriter, r *http.Request) {
	stored, err := service.GetSetting(service.SettingLLMSystemPrompt)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	def := service.DefaultSystemPrompt()
	if strings.TrimSpace(stored) == "" {
		writeJSON(w, http.StatusOK, model.ArtPromptResponse{Prompt: def, IsDefault: true, Default: def})
		return
	}
	writeJSON(w, http.StatusOK, model.ArtPromptResponse{Prompt: stored, IsDefault: false, Default: def})
}

// PUT /api/v1/admin/art/llm-prompt — body: {prompt}; empty string = revert to default
func (h *AdminArtHandler) SetPrompt(w http.ResponseWriter, r *http.Request) {
	var req model.ArtPromptUpdateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid body"})
		return
	}
	trimmed := strings.TrimSpace(req.Prompt)
	if trimmed == "" {
		if err := service.DeleteSetting(service.SettingLLMSystemPrompt); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]string{"message": "reverted to default"})
		return
	}
	if err := service.SetSetting(service.SettingLLMSystemPrompt, req.Prompt); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"message": "saved"})
}

// GET /api/v1/admin/art/stats
func (h *AdminArtHandler) Stats(w http.ResponseWriter, r *http.Request) {
	totalToday, usersHit, total7d, users7d, err := service.ArtStats()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, model.ArtStatsResponse{
		Date:          todayString(),
		TotalToday:    totalToday,
		UsersTodayHit: usersHit,
		Total7d:       total7d,
		Users7d:       users7d,
		Limit:         service.EffectiveDailyLimit(h.cfg),
		Enabled:       service.LLMEnabled(),
		Configured:    h.cfg.DeepSeekAPIKey != "",
	})
}

// GET /api/v1/admin/art/settings
func (h *AdminArtHandler) GetSettings(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, model.ArtSettingsResponse{
		DailyLimit: service.EffectiveDailyLimit(h.cfg),
		Enabled:    service.LLMEnabled(),
		Configured: h.cfg.DeepSeekAPIKey != "",
		Model:      service.EffectiveModel(h.cfg),
		Models:     service.AllowedLLMModels,
		BaseURL:    h.cfg.DeepSeekBaseURL,
	})
}

// PUT /api/v1/admin/art/settings — body: {dailyLimit?, enabled?}
func (h *AdminArtHandler) SetSettings(w http.ResponseWriter, r *http.Request) {
	var req model.ArtSettingsUpdateRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid body"})
		return
	}
	if req.DailyLimit != nil {
		v := *req.DailyLimit
		if v < 0 {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "dailyLimit must be >= 0"})
			return
		}
		if v > 10000 {
			v = 10000
		}
		if err := service.SetSetting(service.SettingLLMDailyLimit, itoa(v)); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}
	}
	if req.Enabled != nil {
		val := "on"
		if !*req.Enabled {
			val = "off"
		}
		if err := service.SetSetting(service.SettingLLMEnabled, val); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}
	}
	if req.Model != nil {
		m := *req.Model
		if !service.IsAllowedLLMModel(m) {
			writeJSON(w, http.StatusBadRequest, map[string]string{
				"error": "model must be one of: " + strings.Join(service.AllowedLLMModels, ", "),
			})
			return
		}
		if err := service.SetSetting(service.SettingLLMModel, m); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}
	}
	h.GetSettings(w, r)
}

// GET /api/v1/admin/art/logs?limit=&offset=&user_id=&mode=&status=
func (h *AdminArtHandler) ListLogs(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	limit, _ := strconv.Atoi(q.Get("limit"))
	offset, _ := strconv.Atoi(q.Get("offset"))
	items, total, err := service.ListLLMLogs(service.LLMLogQuery{
		UserID: q.Get("user_id"),
		Mode:   q.Get("mode"),
		Status: q.Get("status"),
		Limit:  limit,
		Offset: offset,
	})
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	if offset < 0 {
		offset = 0
	}
	writeJSON(w, http.StatusOK, model.LLMRequestLogListResponse{
		Items:  items,
		Total:  total,
		Limit:  limit,
		Offset: offset,
	})
}

// GET /api/v1/admin/art/logs/{id} — full row including request/response payloads.
func (h *AdminArtHandler) GetLogDetail(w http.ResponseWriter, r *http.Request) {
	id, err := strconv.ParseInt(chi.URLParam(r, "id"), 10, 64)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid id"})
		return
	}
	row, err := service.GetLLMLog(id)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	if row == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "not found"})
		return
	}
	writeJSON(w, http.StatusOK, row)
}

// GET /api/v1/admin/art/jobs?limit=&offset=&user_id=&mode=&status=
//
// Lists rows from the async LLM queue (llm_jobs). Distinct from /logs which
// shows DeepSeek call attempts (llm_request_logs) — a single job may produce
// 1-2 attempt rows, but the job row carries the user-visible flow state.
func (h *AdminArtHandler) ListJobs(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	limit, _ := strconv.Atoi(q.Get("limit"))
	offset, _ := strconv.Atoi(q.Get("offset"))
	items, total, err := service.ListLLMJobsAdmin(service.LLMJobAdminQuery{
		UserID: q.Get("user_id"),
		Mode:   q.Get("mode"),
		Status: q.Get("status"),
		Limit:  limit,
		Offset: offset,
	})
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	if offset < 0 {
		offset = 0
	}
	writeJSON(w, http.StatusOK, model.LLMJobListResponse{
		Items:  items,
		Total:  total,
		Limit:  limit,
		Offset: offset,
	})
}

// GET /api/v1/admin/art/jobs/{id} — full row including request_body + result_scene.
func (h *AdminArtHandler) GetJobDetail(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	if id == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid id"})
		return
	}
	row, err := service.GetLLMJobDetailAdmin(id)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	if row == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "not found"})
		return
	}
	writeJSON(w, http.StatusOK, row)
}
