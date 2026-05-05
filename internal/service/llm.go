package service

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/pkn/api/internal/config"
	"github.com/pkn/api/internal/database"
	"github.com/pkn/api/internal/model"
)

var (
	ErrQuotaExceeded = errors.New("daily AI quota exceeded")
	ErrLLMUpstream   = errors.New("LLM upstream error")
	ErrLLMTimeout    = errors.New("LLM upstream timeout")
	ErrLLMDisabled   = errors.New("LLM not configured")
)

// Whitelist used to validate LLM output and clamp it back to the studio's vocabulary.
var (
	llmPalettes = map[string]bool{
		"poster-bright": true, "museum-pop": true, "soft-electric": true,
		"forest-calm": true, "sunset-coral": true, "ocean-mist": true,
		"pastel-garden": true, "mono-bold": true, "tropical-punch": true,
		"vintage-press": true,
	}
	llmCompositions = map[string]bool{
		"row": true, "ring": true, "tower": true, "constellation": true,
		"mirror": true, "solo-hero": true, "wave": true, "vortex": true,
		"mandala": true, "cascade": true, "horizon": true, "petal": true,
	}
	llmMaterialMoods = map[string]bool{
		"glow-heavy": true, "metal-heavy": true, "matte-heavy": true,
		"glass-heavy": true, "balanced": true,
	}
	llmMotionMoods = map[string]bool{
		"still": true, "drifting": true, "spinning": true,
		"pulsing": true, "orbital": true,
	}
)

const llmTitleMaxRunes = 40
const llmTextPhraseMaxRunes = 60

// GetQuota returns today's used count, remaining, and configured limit for a user.
func GetQuota(userID string, limit int) (used, remaining int, err error) {
	today := todayLocal()
	row := database.DB.QueryRow(
		`SELECT count FROM llm_usage WHERE user_id = ? AND date = ? LIMIT 1`,
		userID, today,
	)
	var count int
	err = row.Scan(&count)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, limit, nil
	}
	if err != nil {
		return 0, 0, err
	}
	r := limit - count
	if r < 0 {
		r = 0
	}
	return count, r, nil
}

func incrementUsage(userID string) (newCount int, err error) {
	today := todayLocal()
	_, err = database.DB.Exec(
		`INSERT INTO llm_usage (user_id, date, count) VALUES (?, ?, 1)
		 ON DUPLICATE KEY UPDATE count = count + 1`,
		userID, today,
	)
	if err != nil {
		return 0, err
	}
	row := database.DB.QueryRow(
		`SELECT count FROM llm_usage WHERE user_id = ? AND date = ?`,
		userID, today,
	)
	if err := row.Scan(&newCount); err != nil {
		return 0, err
	}
	return newCount, nil
}

// GenerateLLMDirection: check quota → call DeepSeek → validate → increment usage on success.
func GenerateLLMDirection(
	ctx context.Context,
	cfg *config.Config,
	userID string,
	mode model.LLMMode,
	req model.LLMRequest,
) (model.LLMDirection, int, int, error) {
	if cfg.DeepSeekAPIKey == "" {
		return model.LLMDirection{}, 0, 0, ErrLLMDisabled
	}

	_, remaining, err := GetQuota(userID, cfg.LLMDailyLimit)
	if err != nil {
		return model.LLMDirection{}, 0, 0, err
	}
	if remaining <= 0 {
		return model.LLMDirection{}, 0, 0, ErrQuotaExceeded
	}

	dir, err := callDeepSeek(ctx, cfg, mode, req)
	if err != nil {
		return model.LLMDirection{}, 0, 0, err
	}

	newCount, err := incrementUsage(userID)
	if err != nil {
		return model.LLMDirection{}, 0, 0, err
	}
	r := cfg.LLMDailyLimit - newCount
	if r < 0 {
		r = 0
	}
	return dir, newCount, r, nil
}

// ─── DeepSeek HTTP client ────────────────────────────────────────────────

type deepseekMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type deepseekRequest struct {
	Model          string             `json:"model"`
	Messages       []deepseekMessage  `json:"messages"`
	ResponseFormat map[string]string  `json:"response_format,omitempty"`
	MaxTokens      int                `json:"max_tokens"`
	Temperature    float64            `json:"temperature"`
}

type deepseekChoice struct {
	Message deepseekMessage `json:"message"`
}

type deepseekResponse struct {
	Choices []deepseekChoice `json:"choices"`
	Error   *struct {
		Message string `json:"message"`
		Type    string `json:"type"`
	} `json:"error,omitempty"`
}

var deepseekHTTPClient = &http.Client{Timeout: 12 * time.Second}

func callDeepSeek(ctx context.Context, cfg *config.Config, mode model.LLMMode, req model.LLMRequest) (model.LLMDirection, error) {
	systemPrompt := buildSystemPrompt()
	userPrompt := buildUserPrompt(mode, req)

	dir, err := doDeepSeekCall(ctx, cfg, systemPrompt, userPrompt, 0.9)
	if err == nil {
		return dir, nil
	}
	// Retry once with lower temperature on empty/invalid JSON (per DeepSeek docs caveat).
	if errors.Is(err, ErrLLMUpstream) {
		dir2, err2 := doDeepSeekCall(ctx, cfg, systemPrompt, userPrompt+"\n\nReturn STRICT JSON ONLY, no prose, no markdown.", 0.5)
		if err2 == nil {
			return dir2, nil
		}
		return model.LLMDirection{}, err2
	}
	return model.LLMDirection{}, err
}

func doDeepSeekCall(ctx context.Context, cfg *config.Config, systemPrompt, userPrompt string, temperature float64) (model.LLMDirection, error) {
	body := deepseekRequest{
		Model: cfg.DeepSeekModel,
		Messages: []deepseekMessage{
			{Role: "system", Content: systemPrompt},
			{Role: "user", Content: userPrompt},
		},
		ResponseFormat: map[string]string{"type": "json_object"},
		MaxTokens:      256,
		Temperature:    temperature,
	}
	buf, err := json.Marshal(body)
	if err != nil {
		return model.LLMDirection{}, err
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, cfg.DeepSeekBaseURL+"/chat/completions", bytes.NewReader(buf))
	if err != nil {
		return model.LLMDirection{}, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+cfg.DeepSeekAPIKey)

	resp, err := deepseekHTTPClient.Do(httpReq)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			return model.LLMDirection{}, ErrLLMTimeout
		}
		return model.LLMDirection{}, fmt.Errorf("%w: %v", ErrLLMUpstream, err)
	}
	defer resp.Body.Close()

	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return model.LLMDirection{}, fmt.Errorf("%w: status=%d body=%s", ErrLLMUpstream, resp.StatusCode, string(raw))
	}

	var ds deepseekResponse
	if err := json.Unmarshal(raw, &ds); err != nil {
		return model.LLMDirection{}, fmt.Errorf("%w: parse: %v", ErrLLMUpstream, err)
	}
	if ds.Error != nil {
		return model.LLMDirection{}, fmt.Errorf("%w: %s", ErrLLMUpstream, ds.Error.Message)
	}
	if len(ds.Choices) == 0 || strings.TrimSpace(ds.Choices[0].Message.Content) == "" {
		return model.LLMDirection{}, fmt.Errorf("%w: empty content", ErrLLMUpstream)
	}

	var dir model.LLMDirection
	if err := json.Unmarshal([]byte(ds.Choices[0].Message.Content), &dir); err != nil {
		return model.LLMDirection{}, fmt.Errorf("%w: invalid direction json: %v", ErrLLMUpstream, err)
	}
	if err := validateAndClampDirection(&dir); err != nil {
		return model.LLMDirection{}, fmt.Errorf("%w: %v", ErrLLMUpstream, err)
	}
	return dir, nil
}

func validateAndClampDirection(d *model.LLMDirection) error {
	if !llmPalettes[d.PaletteID] {
		return fmt.Errorf("invalid paletteId: %q", d.PaletteID)
	}
	if !llmCompositions[d.CompositionID] {
		return fmt.Errorf("invalid compositionId: %q", d.CompositionID)
	}
	if !llmMaterialMoods[d.MaterialMood] {
		d.MaterialMood = "balanced"
	}
	if !llmMotionMoods[d.MotionMood] {
		d.MotionMood = "drifting"
	}
	d.Title = clampRunes(strings.TrimSpace(d.Title), llmTitleMaxRunes)
	if d.Title == "" {
		return fmt.Errorf("empty title")
	}
	d.TextPhrase = clampRunes(strings.TrimSpace(d.TextPhrase), llmTextPhraseMaxRunes)
	return nil
}

func clampRunes(s string, max int) string {
	if max <= 0 {
		return ""
	}
	r := []rune(s)
	if len(r) <= max {
		return s
	}
	return string(r[:max])
}

// ─── Prompt builders ─────────────────────────────────────────────────────

func buildSystemPrompt() string {
	return `Bạn là "art director" cho một studio nghệ thuật 3D minimal trên giấy (paper-tone aesthetic). Mỗi lần được gọi, bạn trả VỀ JSON đúng schema dưới đây để hướng dẫn engine sinh layout. Engine xử lý hình học/spacing — bạn chỉ chọn hướng cảm xúc và thẩm mỹ.

OUTPUT: chỉ JSON object, không markdown, không lời dẫn. Schema:
{
  "paletteId": <one of: poster-bright, museum-pop, soft-electric, forest-calm, sunset-coral, ocean-mist, pastel-garden, mono-bold, tropical-punch, vintage-press>,
  "compositionId": <one of: row, ring, tower, constellation, mirror, solo-hero, wave, vortex, mandala, cascade, horizon, petal>,
  "materialMood": <one of: glow-heavy, metal-heavy, matte-heavy, glass-heavy, balanced>,
  "motionMood": <one of: still, drifting, spinning, pulsing, orbital>,
  "title": <tiếng Việt, ≤ 40 ký tự, gợi cảm, có thể dùng dấu chấm/·>,
  "textPhrase": <tiếng Việt ngắn ≤ 60 ký tự, có thể bỏ trống ""; là câu thơ/thì thầm xuất hiện trong cảnh>
}

VÍ DỤ JSON output:
{"paletteId":"sunset-coral","compositionId":"solo-hero","materialMood":"glow-heavy","motionMood":"pulsing","title":"Đoá rực giữa lặng","textPhrase":"khẽ thở · sáng dần"}

Quy tắc:
- Đa dạng giữa các lần gọi; tránh trùng paletteId/compositionId trong "previous" (nếu có).
- Polish: thay đổi nhẹ, giữ tinh thần; Remix: dám đổi palette + composition.
- "title" tránh tiếng Anh; ưu tiên chất thơ ngắn gọn.`
}

func buildUserPrompt(mode model.LLMMode, req model.LLMRequest) string {
	var sb strings.Builder
	sb.WriteString("mode: ")
	sb.WriteString(string(mode))
	sb.WriteString("\n")

	if req.Previous != nil {
		sb.WriteString("previous: ")
		b, _ := json.Marshal(req.Previous)
		sb.Write(b)
		sb.WriteString("\n")
	}
	if req.Scene != nil {
		sb.WriteString("currentScene: ")
		b, _ := json.Marshal(req.Scene)
		sb.Write(b)
		sb.WriteString("\n")
	}
	sb.WriteString("\nReturn the json direction now.")
	return sb.String()
}
