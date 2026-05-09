package service

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"strconv"
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
	ErrLLMOff        = errors.New("LLM disabled by admin")
)

func EffectiveDailyLimit(cfg *config.Config) int {
	v, _ := GetSetting(SettingLLMDailyLimit)
	if v != "" {
		if n, err := strconv.Atoi(strings.TrimSpace(v)); err == nil && n > 0 {
			return n
		}
	}
	return cfg.LLMDailyLimit
}

func LLMEnabled() bool {
	v, _ := GetSetting(SettingLLMEnabled)
	if strings.TrimSpace(v) == "off" {
		return false
	}
	return true
}

// Whitelists: only enums the renderer can actually handle. The LLM owns the
// rest (positions, sizes, colors per shape) — no composition/harmony/exotic
// vocabulary anymore; the prompt teaches layout as math.
var (
	llmPalettes = map[string]bool{
		"poster-bright": true, "museum-pop": true, "soft-electric": true,
		"forest-calm": true, "sunset-coral": true, "ocean-mist": true,
		"pastel-garden": true, "mono-bold": true, "tropical-punch": true,
		"vintage-press": true,
	}
	llmShapeKinds = map[string]bool{
		"sphere": true, "box": true, "torus": true, "knot": true,
		"panel": true, "cone": true, "cylinder": true, "capsule": true,
		"icosahedron": true, "octahedron": true, "disc": true,
	}
	llmMaterials = map[string]bool{
		"matte": true, "glass": true, "metal": true, "glow": true,
	}
	llmMotions = map[string]bool{
		"still": true, "float": true, "spin": true, "orbit": true, "pulse": true,
	}
	llmFonts = map[string]bool{
		"sans": true, "serif": true, "round": true, "square": true,
	}
	llmTextAligns = map[string]bool{
		"left": true, "center": true, "right": true,
	}
)

const (
	llmTitleMaxRunes   = 40
	llmAINotesMaxRunes = 200
	llmTextMaxRunes    = 120
	llmSceneVersion    = 3
	llmShapeMaxCount   = 100
	llmShapeMinCount   = 1
	llmTextMaxCount    = 4
)

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

// ReserveLLMQuota atomically charges a quota slot for an async job submission.
// Returns ErrQuotaExceeded when the user is at limit. The async path uses this
// at submit time so the user can't queue more jobs than their daily allowance.
// On failure (worker error, cancel-while-pending), pair with RefundLLMQuota.
//
// Concurrent-submit race window: small and bounded by frontend's aiBusyRef +
// daily 5-job ceiling — at worst a user could overshoot by 1 with hand-crafted
// requests. Acceptable for this scale; no SELECT FOR UPDATE needed.
func ReserveLLMQuota(userID string, limit int) (used, remaining int, err error) {
	_, rem, err := GetQuota(userID, limit)
	if err != nil {
		return 0, 0, err
	}
	if rem <= 0 {
		return limit, 0, ErrQuotaExceeded
	}
	newCount, err := incrementUsage(userID)
	if err != nil {
		return 0, 0, err
	}
	r := limit - newCount
	if r < 0 {
		r = 0
	}
	return newCount, r, nil
}

// RefundLLMQuota decrements today's usage counter. Used when an async job
// fails (DeepSeek upstream/timeout/validation) or is canceled before the
// worker started. GREATEST(count-1, 0) keeps the counter from going negative
// even if refund is somehow called twice.
func RefundLLMQuota(userID string) error {
	today := todayLocal()
	_, err := database.DB.Exec(
		`UPDATE llm_usage SET count = GREATEST(count - 1, 0)
		 WHERE user_id = ? AND date = ?`,
		userID, today,
	)
	return err
}

// RunLLMJob is the worker entry point: build prompts, call DeepSeek (with
// internal retry), validate. Same code path as the synchronous handler but
// without quota touching — the async submit already reserved a slot.
func RunLLMJob(ctx context.Context, cfg *config.Config, userID, username string, mode model.LLMMode, req model.LLMRequest) (model.LLMScene, error) {
	if cfg.DeepSeekAPIKey == "" {
		return model.LLMScene{}, ErrLLMDisabled
	}
	if !LLMEnabled() {
		return model.LLMScene{}, ErrLLMOff
	}
	return callDeepSeek(ctx, cfg, userID, username, mode, req)
}

// GenerateLLMScene: check quota → call DeepSeek (with retry) → validate → increment usage.
// LLM authors the full LLMScene; backend just sanity-clamps.
func GenerateLLMScene(
	ctx context.Context,
	cfg *config.Config,
	userID string,
	username string,
	mode model.LLMMode,
	req model.LLMRequest,
) (model.LLMScene, int, int, error) {
	if cfg.DeepSeekAPIKey == "" {
		return model.LLMScene{}, 0, 0, ErrLLMDisabled
	}
	if !LLMEnabled() {
		return model.LLMScene{}, 0, 0, ErrLLMOff
	}

	limit := EffectiveDailyLimit(cfg)
	_, remaining, err := GetQuota(userID, limit)
	if err != nil {
		return model.LLMScene{}, 0, 0, err
	}
	if remaining <= 0 {
		return model.LLMScene{}, 0, 0, ErrQuotaExceeded
	}

	scene, err := callDeepSeek(ctx, cfg, userID, username, mode, req)
	if err != nil {
		return model.LLMScene{}, 0, 0, err
	}

	newCount, err := incrementUsage(userID)
	if err != nil {
		return model.LLMScene{}, 0, 0, err
	}
	r := limit - newCount
	if r < 0 {
		r = 0
	}
	return scene, newCount, r, nil
}

// ─── DeepSeek HTTP client ────────────────────────────────────────────────

type deepseekMessage struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type deepseekRequest struct {
	Model          string            `json:"model"`
	Messages       []deepseekMessage `json:"messages"`
	ResponseFormat map[string]string `json:"response_format,omitempty"`
	MaxTokens      int               `json:"max_tokens"`
	Temperature    float64           `json:"temperature"`
}

type deepseekChoice struct {
	Message deepseekMessage `json:"message"`
}

type deepseekUsage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
}

type deepseekResponse struct {
	Choices []deepseekChoice `json:"choices"`
	Usage   deepseekUsage    `json:"usage"`
	Error   *struct {
		Message string `json:"message"`
		Type    string `json:"type"`
	} `json:"error,omitempty"`
}

// 100s per HTTP call — matches Cloudflare Free tier's hard 100s proxy ceiling.
// AI-first refactor pushed v4-pro reasoning + full-recipe output past the old
// 60s budget for polish/remix (which process currentScene + author from
// scratch). Handler ctx (110s) wraps this with margin for retry on fast-fail
// (JSON parse), but not for retry-after-timeout — Cloudflare would have
// already cut us off.
var deepseekHTTPClient = &http.Client{Timeout: 100 * time.Second}

func callDeepSeek(ctx context.Context, cfg *config.Config, userID, username string, mode model.LLMMode, req model.LLMRequest) (model.LLMScene, error) {
	systemPrompt := buildSystemPrompt()
	userPrompt := buildUserPrompt(mode, req)

	scene, err := doDeepSeekCall(ctx, cfg, userID, username, mode, 1, systemPrompt, userPrompt, 0.9)
	if err == nil {
		return scene, nil
	}
	// Retry once on upstream/parse/empty-shapes failures with stricter prompt + lower temp.
	if errors.Is(err, ErrLLMUpstream) {
		retryUser := userPrompt + "\n\nSTRICT JSON ONLY. Output đúng schema LLMScene v3. Mọi shape phải có đầy đủ: id, shape, color, material, motion, position[3], size[3], scale. Tối thiểu 4 shapes."
		scene2, err2 := doDeepSeekCall(ctx, cfg, userID, username, mode, 2, systemPrompt, retryUser, 0.5)
		if err2 == nil {
			return scene2, nil
		}
		return model.LLMScene{}, err2
	}
	return model.LLMScene{}, err
}

func doDeepSeekCall(
	ctx context.Context,
	cfg *config.Config,
	userID, username string,
	mode model.LLMMode,
	attempt int,
	systemPrompt, userPrompt string,
	temperature float64,
) (model.LLMScene, error) {
	resolvedModel := EffectiveModel(cfg)
	body := deepseekRequest{
		Model: resolvedModel,
		Messages: []deepseekMessage{
			{Role: "system", Content: systemPrompt},
			{Role: "user", Content: userPrompt},
		},
		ResponseFormat: map[string]string{"type": "json_object"},
		// Full-recipe JSON for 100 shapes ≈ 12k tokens. v4-pro's reasoning
		// chain eats 2–3× internal tokens on top. 16384 leaves margin so the
		// JSON tail isn't truncated when the LLM packs the cap.
		MaxTokens:   16384,
		Temperature: temperature,
	}
	buf, _ := json.Marshal(body)

	logIn := LLMLogInput{
		UserID:         userID,
		Username:       username,
		Mode:           string(mode),
		Model:          resolvedModel,
		Attempt:        attempt,
		Temperature:    temperature,
		RequestPayload: buf,
	}
	start := time.Now()
	defer func() {
		logIn.LatencyMs = int(time.Since(start) / time.Millisecond)
		if logIn.Status == "" {
			logIn.Status = "unknown"
		}
		if err := RecordLLMLog(logIn); err != nil {
			log.Printf("[llm] record log failed: %v", err)
		}
	}()

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, cfg.DeepSeekBaseURL+"/chat/completions", bytes.NewReader(buf))
	if err != nil {
		logIn.Status = "request_build_error"
		logIn.ErrorMessage = err.Error()
		return model.LLMScene{}, err
	}
	httpReq.Header.Set("Content-Type", "application/json")
	httpReq.Header.Set("Authorization", "Bearer "+cfg.DeepSeekAPIKey)

	resp, err := deepseekHTTPClient.Do(httpReq)
	if err != nil {
		if errors.Is(err, context.DeadlineExceeded) {
			logIn.Status = "timeout"
			logIn.ErrorMessage = err.Error()
			return model.LLMScene{}, ErrLLMTimeout
		}
		log.Printf("[llm] http error: %v", err)
		logIn.Status = "http_error"
		logIn.ErrorMessage = err.Error()
		return model.LLMScene{}, fmt.Errorf("%w: %v", ErrLLMUpstream, err)
	}
	defer resp.Body.Close()

	raw, readErr := io.ReadAll(resp.Body)
	logIn.ResponseRaw = raw
	if readErr != nil {
		if errors.Is(readErr, context.DeadlineExceeded) || errors.Is(readErr, context.Canceled) || ctx.Err() != nil {
			logIn.Status = "timeout"
			logIn.ErrorMessage = readErr.Error()
			return model.LLMScene{}, ErrLLMTimeout
		}
		log.Printf("[llm] body read error: %v", readErr)
		logIn.Status = "body_read_error"
		logIn.ErrorMessage = readErr.Error()
		return model.LLMScene{}, fmt.Errorf("%w: read body: %v", ErrLLMUpstream, readErr)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		log.Printf("[llm] upstream %d: %s", resp.StatusCode, truncateForLog(raw))
		logIn.Status = fmt.Sprintf("upstream_%d", resp.StatusCode)
		logIn.ErrorMessage = string(raw)
		return model.LLMScene{}, fmt.Errorf("%w: status=%d", ErrLLMUpstream, resp.StatusCode)
	}

	var ds deepseekResponse
	if err := json.Unmarshal(raw, &ds); err != nil {
		log.Printf("[llm] parse top-level: %v body=%s", err, truncateForLog(raw))
		logIn.Status = "parse_error"
		logIn.ErrorMessage = err.Error()
		return model.LLMScene{}, fmt.Errorf("%w: parse: %v", ErrLLMUpstream, err)
	}
	logIn.PromptTokens = ds.Usage.PromptTokens
	logIn.CompletionTokens = ds.Usage.CompletionTokens
	logIn.TotalTokens = ds.Usage.TotalTokens

	if ds.Error != nil {
		log.Printf("[llm] upstream error: %s", ds.Error.Message)
		logIn.Status = "upstream_error"
		logIn.ErrorMessage = ds.Error.Message
		return model.LLMScene{}, fmt.Errorf("%w: %s", ErrLLMUpstream, ds.Error.Message)
	}
	if len(ds.Choices) == 0 || strings.TrimSpace(ds.Choices[0].Message.Content) == "" {
		log.Printf("[llm] empty content: %s", truncateForLog(raw))
		logIn.Status = "empty_content"
		return model.LLMScene{}, fmt.Errorf("%w: empty content", ErrLLMUpstream)
	}

	content := ds.Choices[0].Message.Content
	var scene model.LLMScene
	if err := json.Unmarshal([]byte(content), &scene); err != nil {
		log.Printf("[llm] invalid scene json: %v content=%s", err, truncateForLog([]byte(content)))
		logIn.Status = "invalid_scene_json"
		logIn.ErrorMessage = err.Error()
		return model.LLMScene{}, fmt.Errorf("%w: invalid scene json: %v", ErrLLMUpstream, err)
	}
	if err := validateAndClampScene(&scene); err != nil {
		log.Printf("[llm] scene validation failed: %v content=%s", err, truncateForLog([]byte(content)))
		logIn.Status = "validation_error"
		logIn.ErrorMessage = err.Error()
		return model.LLMScene{}, fmt.Errorf("%w: %v", ErrLLMUpstream, err)
	}
	logIn.Status = "success"
	logIn.ParsedScene = &scene
	return scene, nil
}

func truncateForLog(b []byte) string {
	const max = 600
	if len(b) <= max {
		return string(b)
	}
	return string(b[:max]) + "…"
}

// validateAndClampScene asserts the LLM output is renderable. Reject hard
// errors (bad palette, empty shapes); clamp soft errors (out-of-bbox positions,
// invalid enum values default-fix); cap arrays.
func validateAndClampScene(s *model.LLMScene) error {
	if s.Version != llmSceneVersion {
		// Be lenient — LLM sometimes emits version 1/2/3 inconsistently. Force v3.
		s.Version = llmSceneVersion
	}
	if !llmPalettes[s.PaletteID] {
		return fmt.Errorf("invalid paletteId: %q", s.PaletteID)
	}
	s.Title = clampRunes(strings.TrimSpace(s.Title), llmTitleMaxRunes)
	if s.Title == "" {
		return fmt.Errorf("empty title")
	}
	s.AINotes = clampRunes(strings.TrimSpace(s.AINotes), llmAINotesMaxRunes)
	s.Background = strings.TrimSpace(s.Background)

	if len(s.Shapes) == 0 {
		return fmt.Errorf("scene has no shapes")
	}
	if len(s.Shapes) > llmShapeMaxCount {
		s.Shapes = s.Shapes[:llmShapeMaxCount]
	}
	for i := range s.Shapes {
		clampShape(&s.Shapes[i], i)
	}
	if len(s.Shapes) < llmShapeMinCount {
		return fmt.Errorf("too few shapes after sanitize")
	}

	if len(s.Texts) > llmTextMaxCount {
		s.Texts = s.Texts[:llmTextMaxCount]
	}
	for i := range s.Texts {
		clampText(&s.Texts[i], i)
	}
	return nil
}

func clampShape(sh *model.LLMShape, idx int) {
	sh.ID = strings.TrimSpace(sh.ID)
	if sh.ID == "" {
		sh.ID = fmt.Sprintf("s_%d", idx)
	}
	if !llmShapeKinds[sh.Shape] {
		sh.Shape = "sphere"
	}
	if !llmMaterials[sh.Material] {
		sh.Material = "matte"
	}
	if !llmMotions[sh.Motion] {
		sh.Motion = "still"
	}
	sh.Color = strings.TrimSpace(sh.Color)
	if !looksLikeHex(sh.Color) {
		sh.Color = "#111111"
	}
	sh.Position[0] = clampFloat(sh.Position[0], -2.5, 2.5)
	sh.Position[1] = clampFloat(sh.Position[1], -1.6, 1.6)
	sh.Position[2] = clampFloat(sh.Position[2], -1.0, 1.0)
	for j := 0; j < 3; j++ {
		if sh.Size[j] <= 0 {
			sh.Size[j] = 1.0
		}
		sh.Size[j] = clampFloat(sh.Size[j], 0.3, 4.0)
	}
	if sh.Scale <= 0 {
		sh.Scale = 1.0
	}
	sh.Scale = clampFloat(sh.Scale, 0.4, 2.4)
	sh.Name = clampRunes(strings.TrimSpace(sh.Name), 40)
	if sh.Rotation != nil {
		for j := 0; j < 3; j++ {
			sh.Rotation[j] = clampFloat(sh.Rotation[j], -3.2, 3.2)
		}
	}
}

func clampText(t *model.LLMText, idx int) {
	t.ID = strings.TrimSpace(t.ID)
	if t.ID == "" {
		t.ID = fmt.Sprintf("t_%d", idx)
	}
	t.Content = clampRunes(strings.TrimSpace(t.Content), llmTextMaxRunes)
	if !llmFonts[t.Font] {
		t.Font = "sans"
	}
	if !llmTextAligns[t.Align] {
		t.Align = "center"
	}
	if !llmMaterials[t.Material] {
		t.Material = "matte"
	}
	if !llmMotions[t.Motion] {
		t.Motion = "still"
	}
	t.Color = strings.TrimSpace(t.Color)
	if !looksLikeHex(t.Color) {
		t.Color = "#111111"
	}
	t.Position[0] = clampFloat(t.Position[0], -2.5, 2.5)
	t.Position[1] = clampFloat(t.Position[1], -1.6, 1.6)
	t.Position[2] = clampFloat(t.Position[2], -1.0, 1.0)
	if t.Scale <= 0 {
		t.Scale = 1.4
	}
	t.Scale = clampFloat(t.Scale, 0.8, 2.4)
	t.Name = clampRunes(strings.TrimSpace(t.Name), 40)
	if t.Rotation != nil {
		for j := 0; j < 3; j++ {
			t.Rotation[j] = clampFloat(t.Rotation[j], -3.2, 3.2)
		}
	}
}

func looksLikeHex(s string) bool {
	if len(s) != 4 && len(s) != 7 {
		return false
	}
	if s[0] != '#' {
		return false
	}
	for i := 1; i < len(s); i++ {
		c := s[i]
		ok := (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') || (c >= 'A' && c <= 'F')
		if !ok {
			return false
		}
	}
	return true
}

func clampFloat(v, min, max float64) float64 {
	if v < min {
		return min
	}
	if v > max {
		return max
	}
	return v
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
	if v, _ := GetSetting(SettingLLMSystemPrompt); strings.TrimSpace(v) != "" {
		return v
	}
	return DefaultSystemPrompt()
}

// DefaultSystemPrompt is the AI-first "art director" instruction set. ~3500 tokens.
// Sections: role, canvas, palette catalog, shapes, materials, motions, layout vocabulary, schema + few-shot.
func DefaultSystemPrompt() string {
	return `Bạn là **art director** của một studio nghệ thuật 3D minimal trên giấy (Vietnamese paper-tone aesthetic). Bạn KHÔNG chỉ "gợi ý"; bạn TỰ TAY soạn cảnh — quyết định mọi vị trí, kích thước, màu, vật liệu, chuyển động cho từng shape một cách có chủ đích thẩm mỹ.

OUTPUT: chỉ một JSON object đúng schema LLMScene v3 dưới đây. KHÔNG markdown, KHÔNG prose, KHÔNG code fence.

═══════════════════════════════════════════════════════════════
## 1. CANVAS

- Camera **orthographic**, đứng tại (0, 0, 10), nhìn về origin. Y-up, X+ phải, Z+ về phía người xem.
- **Bounding box hữu dụng** (TUYỆT ĐỐI không đặt shape ngoài đây):
  • x ∈ [-2.5, 2.5]   (5 đơn vị ngang — màn hình rộng theo X)
  • y ∈ [-1.6, 1.6]   (3.2 đơn vị dọc — mỏng theo Y)
  • z ∈ [-1.0, 1.0]   (depth nhẹ, chỉ để parallax — KHÔNG dùng z lớn vì sẽ ngoài frame)
- **Đơn vị**: 1 đơn vị ≈ một đốt tay (a knuckle). Shape cỡ 1×1×1 = "trung bình".
- **Lighting**: ambient 0.75 + directional sáng (4,5,6) + point light màu palette.colors[0]. → glow material thấy rõ; matte mềm; metal lấp lánh nhẹ; glass nhẹ trong, không quá lung linh.
- **Background**: lấy từ palette (hoặc override hex của bạn). KHÔNG dùng full đen / full trắng nuốt nền.
- **Scale × Size**: visual size = scale × size_axis. Dùng **size** để định tỉ lệ shape (cao/dẹt/dài), dùng **scale** để định "tầm cỡ trong scene" (lớn/nhỏ). Ví dụ tốt: cylinder cột tháp → size [0.6, 2.4, 0.6], scale 1.0. Ví dụ tệ: size [1,1,1] + scale 2.5 → khó kiểm soát silhouette.

═══════════════════════════════════════════════════════════════
## 2. PALETTE CATALOG (chọn 1 trong 10 paletteId)

Mỗi palette có background tone + 5 swatch. Bạn TỰ chọn màu cho mỗi shape (KHÔNG bắt buộc dùng đúng swatch palette — có thể dùng tone gần kề — nhưng nên DỰA vào palette để giữ nhất quán).

| paletteId | background | colors (5 hex) | mood |
|---|---|---|---|
| **poster-bright** | #f5efe2 | #f03248 #fff3d6 #111111 #2556ff #b9ff3b | poster bảo tàng, tươi rực, đỏ son hero |
| **museum-pop** | #fff8e8 | #2556ff #ffce2e #f5efe2 #111111 #ff7a1a | gallery treo poster, mạnh + ấm |
| **soft-electric** | #f7f1ff | #8a4dff #f8e7ff #36d6c9 #111111 #ffd447 | tím lavender + ngọc, tone synthwave nhẹ |
| **forest-calm** | #f1ecd9 | #3d6e4f #c87455 #e8c46c #1a1a1a #f3ecd5 | rừng + đất nung + lúa, tĩnh |
| **sunset-coral** | #fff2e0 | #ff6b5e #ffb8a3 #1c2a4f #ffc857 #fff1de | hoàng hôn cam + indigo, ấm áp |
| **ocean-mist** | #eaf3f6 | #0d6e8c #a8d5e3 #f47c6f #101820 #f5ecd5 | biển sương + gạch coral, mát |
| **pastel-garden** | #fbf2f7 | #f5a3c7 #a3e0c4 #c7b3f5 #3a3a4a #fff5ec | vườn pastel hồng-xanh-tím, nhẹ |
| **mono-bold** | #f3f1ea | #0a0a0a #5a5a5a #e63946 #ffd23f #f7f4eb | đen trắng + đỏ vàng, thiền + mạnh |
| **tropical-punch** | #fff7e6 | #e0218a #c8ff3b #1ec9c4 #101010 #fff5e0 | nhiệt đới rực, hồng + chanh + ngọc |
| **vintage-press** | #efe6cf | #8b2635 #d4a541 #7a8a64 #1f1a14 #f0e6d0 | giấy vintage, đỏ rượu + vàng đồng + xanh rêu |

═══════════════════════════════════════════════════════════════
## 3. SHAPE PRIMITIVES (11 kinds)

Mỗi shape có đặc tính hình học riêng. Chọn kind PHÙ HỢP với ý đồ (đừng đặt sphere ở đâu cũng dùng).

- **sphere**: khối tròn đặc, mềm — đọc như viên ngọc, hành tinh. Tỉ lệ tốt: ~đều w≈h≈d, kích cỡ tự do.
- **box**: khối lập phương / hộp — đọc như viên gạch, kiến trúc. Tỉ lệ tốt: rectangular (w≠h), ít khi vuông tuyệt đối.
- **torus**: vành khuyên (donut) — đọc như vòng, cổng, halo. Mặc định mặt phẳng XY; tỉ lệ tốt size [1.4, 1.4, 0.4].
- **knot**: nút thắt (torus knot) — trang trí phức tạp, tâm điểm thị giác. Dùng tiết kiệm (1 cái/scene). Tỉ lệ ~đều.
- **panel**: tấm phẳng mỏng — đọc như poster/billboard/tấm gỗ. Tỉ lệ tốt: w 1.5–2.5 × h 0.8–1.4 × d 0.08–0.2 (DẸT).
- **cone**: nón — đọc như tháp, cây thông, mũi tên hướng lên. Tỉ lệ tốt: width≈depth, height ≥ width.
- **cylinder**: trụ — cột, lon, ống. Cao thì size [0.6, 2.4, 0.6]; lùn thì [1.2, 0.4, 1.2].
- **capsule**: viên thuốc, trụ đầu tròn — mềm hơn cylinder. Tỉ lệ tương tự.
- **icosahedron**: 20 mặt đều — đọc như đá quý, viên xúc xắc thần. Tỉ lệ ~đều.
- **octahedron**: 8 mặt đều (kim cương 2 chóp) — đọc như viên đá pha lê. Tỉ lệ ~đều, có thể stretch height (size [1, 1.6, 1]).
- **disc**: đĩa tròn dẹt — đọc như đồng xu, mặt trăng. Tỉ lệ: w ≈ h ≈ 1.5 × d 0.2–0.4 (rất dẹt).

**Quy tắc tỉ lệ chung**: TUYỆT ĐỐI tránh size [1, 1, 1] đồng đều cho mọi shape — đọc như placeholder mặc định, kém thẩm mỹ. Mỗi shape phải có **silhouette có chủ đích**.

═══════════════════════════════════════════════════════════════
## 4. MATERIALS (4 kinds)

- **matte**: bề mặt giấy / đất sét, không phản chiếu. Dùng cho khối "neutral", không cần highlight. Phổ biến nhất, baseline.
- **glass**: trong suốt nhẹ (transmission ~35%), bóng nhẹ. Dùng cho 1–2 shape muốn "trôi nổi" / overlay. KHÔNG dùng glass cho text (sẽ tự fallback matte).
- **metal**: phản chiếu vừa (roughness 0.24), highlight đậm. Dùng để tạo "trọng tâm" — không quá 30% scene.
- **glow**: emissive (0.24), tự phát sáng + bloom mềm. Dùng để tạo "ngôi sao" / điểm sáng. Không quá 40% scene (loãng cảm xúc).

**Pairing hints**: matte + glow tương phản tốt; metal + glass khó đứng cùng (cả hai đều bóng); glass + matte hài hoà. Với palette dark (mono-bold, vintage-press), glow là điểm nhấn mạnh; với palette bright (poster-bright, tropical-punch), nên cân matte để không loé.

═══════════════════════════════════════════════════════════════
## 5. MOTIONS (5 kinds)

- **still**: đứng yên. Baseline. Dùng cho shape "kiến trúc".
- **float**: bobbing trục Y, biên độ ±0.12. Đọc như hơi thở. Dùng cho shape mềm (sphere, capsule).
- **spin**: xoay quanh trục Y chậm. Đọc như đồng xu lăn / vành khuyên. Phù hợp torus, knot, disc.
- **orbit**: quay theo vòng XZ quanh origin gốc shape, bán kính ±0.18. Tạo cảm giác hành tinh. Không quá 2 shape/scene (loạn).
- **pulse**: scale breathe ±8% theo sin. Dùng cho shape muốn "thở" (heart, sun).

**Quy tắc**: trộn motion để có nhịp — đừng để mọi shape cùng motion (trừ khi cố ý "still" cho cả scene tĩnh). Hero shape thường lấy motion mạnh hơn (spin/pulse), shape phụ tĩnh hơn (still/float).

═══════════════════════════════════════════════════════════════
## 6. LAYOUT VOCABULARY (math, không phải template ID)

Bạn TỰ chọn pattern bố cục — KHÔNG có composition ID enum. Đặt shape dựa trên hình học:

- **Row** (poster line): same y ≈ 0, x rải đều [-2, -1, 0, 1, 2] với 5 shape; same z; size variation tuỳ.
- **Ring** (mandala light): n shape ở vòng tròn radius r quanh origin. Góc i × (2π/n). VD ring of 5: angles 0°, 72°, 144°, 216°, 288° tại r=1.5 → x = r·cos(θ), z = r·sin(θ), y ≈ 0.
- **Tower** (vertical stack): same x ± 0.3 jitter, y stepped từ -1.5 lên 1.5, mỗi shape height ~0.5 unit để stack.
- **Constellation** (organic spread): 6–14 shape rải tự do bbox, KHÔNG đối xứng, varied scale. Centroid gần origin nhưng không bắt buộc.
- **Mirror**: cặp đối xứng qua trục Y (x = +a và x = -a), thường 4–6 shape.
- **Solo-hero**: 1 shape lớn ở tâm (scale 1.6–2.2), 3–5 shape nhỏ (scale 0.5–0.8) bay lượn quanh.
- **Wave**: y theo hàm sin theo x. VD 7 shape: x = -2..2, y = sin(x·π) × 0.8.
- **Vortex**: golden-angle spiral. Cho i = 0..n-1: θ = i × 137.5°, r = 0.2 × √i; x = r·cos(θ), y = r·sin(θ) (or z).
- **Mandala**: 1 center + n petals quanh (n = 6 thường), mỗi petal cùng kind/size để tạo radial symmetry.
- **Horizon**: 3–4 base shape ở y ≈ -1.2 (dải đất), 1 hero shape ở y ≈ 0.6 (mặt trời / chủ thể), texts ở y > 1.

Bạn được phép TỰ DO mix — ví dụ "ring of 4 + 1 center + 1 floating hero". Layout tự do tốt hơn template cứng.

**Nguyên tắc spacing**: 2 shape gần nhau quá (cách < 0.3 unit) sẽ chồng. Trừ khi cố ý cluster, để spacing ≥ 0.6.

═══════════════════════════════════════════════════════════════
## 7. SCHEMA — LLMScene v3

` + "```" + `json
{
  "version": 3,
  "title": "<tiếng Việt, ≤ 40 ký tự, có thể dùng ' · '>",
  "paletteId": "<one of 10 above>",
  "background": "<optional override hex; bỏ trống = dùng background mặc định của palette>",
  "shapes": [   // 6–40 thường, 1–100 hard cap (scene đông >50 shapes nên ưu tiên cluster + tỉ lệ chênh)
    {
      "id": "s_0",
      "shape": "sphere|box|torus|knot|panel|cone|cylinder|capsule|icosahedron|octahedron|disc",
      "color": "#hex (3 or 6 hex digits)",
      "material": "matte|glass|metal|glow",
      "motion": "still|float|spin|orbit|pulse",
      "position": [x, y, z],     // x∈[-2.5,2.5] y∈[-1.6,1.6] z∈[-1,1]
      "size": [w, h, d],         // mỗi axis 0.3..4.0; KHÔNG dùng [1,1,1] mọi shape
      "scale": <0.4..2.4>,       // outer multiplier
      "rotation": [rx, ry, rz],  // OPTIONAL; bỏ trống → engine tự fill camera-facing tilt
      "name": "<optional Vietnamese label, ≤40 char>"
    }
  ],
  "texts": [   // 0–4
    {
      "id": "t_0",
      "content": "<≤120 ký tự, ≤3 dòng>",
      "font": "sans|serif|round|square",
      "align": "left|center|right",
      "color": "#hex",
      "material": "matte|glass|metal|glow",  // glass tự fallback matte
      "motion": "still|float|spin|orbit|pulse",
      "position": [x, y, z],
      "scale": <0.8..2.4>,
      "rotation": [rx, ry, rz],  // OPTIONAL
      "name": "<optional>"
    }
  ],
  "aiNotes": "<≤200 ký tự, lý giải ngắn ý đồ thẩm mỹ — tiếng Việt; debug only>"
}
` + "```" + `

**RÀNG BUỘC TUYỆT ĐỐI**:
- shapes BẮT BUỘC ≥ 1, hard cap 100 (thường 6–40; scene đông >50 cần spacing chặt hơn để khỏi nuốt nhau). version PHẢI = 3.
- size MỖI shape PHẢI có 3 giá trị > 0, KHÔNG được [1,1,1] đồng đều cho > 50% scene.
- Position trong bbox; tự clamp nếu sát biên.
- texts dùng tiếng Việt thơ ca, ngắn gọn.
- Polish: GIỮ phần lớn shape ID từ currentScene; chỉ tinh chỉnh color/material/motion/position/size. Mục tiêu refine, không chuyển đổi.
- Remix: TỰ DO thêm/bớt/đổi shape; nên giữ ≥ 30% ID nếu currentScene có > 4 shape; đổi paletteId hoặc layout style; aiNotes ghi rõ "đã đổi gì".
- Random (không có currentScene): tạo từ đầu hoàn toàn.

═══════════════════════════════════════════════════════════════
## 8. FEW-SHOT EXEMPLARS

### Exemplar 1 — Solo-hero static, vintage-press, 5 shapes
{"version":3,"title":"Đoá đỏ tĩnh","paletteId":"vintage-press","shapes":[{"id":"s_0","shape":"sphere","color":"#8b2635","material":"glow","motion":"pulse","position":[0,0.2,0],"size":[1.6,1.6,1.6],"scale":1.5,"name":"đoá hồng"},{"id":"s_1","shape":"disc","color":"#d4a541","material":"matte","motion":"still","position":[-1.6,-0.4,-0.3],"size":[1.2,1.2,0.3],"scale":0.7},{"id":"s_2","shape":"disc","color":"#d4a541","material":"matte","motion":"still","position":[1.6,-0.4,0.3],"size":[1.2,1.2,0.3],"scale":0.7},{"id":"s_3","shape":"capsule","color":"#7a8a64","material":"matte","motion":"float","position":[-1.0,1.0,0.2],"size":[0.4,0.9,0.4],"scale":0.8},{"id":"s_4","shape":"capsule","color":"#7a8a64","material":"matte","motion":"float","position":[1.0,1.0,-0.2],"size":[0.4,0.9,0.4],"scale":0.8}],"texts":[{"id":"t_0","content":"khẽ thở","font":"serif","align":"center","color":"#1f1a14","material":"matte","motion":"still","position":[0,1.4,0],"scale":1.4}],"aiNotes":"Hero pulse glow đỏ tâm + 2 đĩa vàng cân + 2 capsule xanh rêu lơ lửng. Tone vintage tĩnh."}

### Exemplar 2 — Constellation, ocean-mist, 11 shapes, mixed materials
{"version":3,"title":"Vô số ánh","paletteId":"ocean-mist","shapes":[{"id":"s_0","shape":"sphere","color":"#0d6e8c","material":"glow","motion":"pulse","position":[0,0.3,0],"size":[1.0,1.0,1.0],"scale":1.4},{"id":"s_1","shape":"sphere","color":"#a8d5e3","material":"glow","motion":"float","position":[-1.8,0.8,0.4],"size":[0.5,0.5,0.5],"scale":0.9},{"id":"s_2","shape":"sphere","color":"#a8d5e3","material":"glow","motion":"float","position":[1.6,-0.6,-0.3],"size":[0.6,0.6,0.6],"scale":0.8},{"id":"s_3","shape":"icosahedron","color":"#f47c6f","material":"matte","motion":"spin","position":[2.0,0.7,0.2],"size":[0.7,0.7,0.7],"scale":0.7},{"id":"s_4","shape":"octahedron","color":"#0d6e8c","material":"metal","motion":"spin","position":[-2.1,-0.5,-0.4],"size":[0.6,1.0,0.6],"scale":0.8},{"id":"s_5","shape":"disc","color":"#f5ecd5","material":"matte","motion":"still","position":[0.8,1.2,-0.5],"size":[0.9,0.9,0.25],"scale":0.6},{"id":"s_6","shape":"disc","color":"#f5ecd5","material":"matte","motion":"still","position":[-0.6,-1.3,0.5],"size":[1.1,1.1,0.25],"scale":0.7},{"id":"s_7","shape":"sphere","color":"#a8d5e3","material":"glow","motion":"pulse","position":[1.2,0.0,0.6],"size":[0.4,0.4,0.4],"scale":1.0},{"id":"s_8","shape":"sphere","color":"#a8d5e3","material":"glow","motion":"pulse","position":[-1.2,0.4,-0.6],"size":[0.4,0.4,0.4],"scale":1.0},{"id":"s_9","shape":"capsule","color":"#101820","material":"matte","motion":"orbit","position":[0,-1.0,0.3],"size":[0.4,0.8,0.4],"scale":0.6},{"id":"s_10","shape":"icosahedron","color":"#f47c6f","material":"matte","motion":"spin","position":[-1.0,1.4,0.0],"size":[0.5,0.5,0.5],"scale":0.6}],"texts":[{"id":"t_0","content":"thiên hà thì thầm","font":"serif","align":"center","color":"#0d6e8c","material":"glow","motion":"pulse","position":[0,1.5,0],"scale":1.6}],"aiNotes":"Hero glow xanh + chùm sao nhỏ rải organic + 2 đá sáng coral cho điểm tương phản."}

### Exemplar 3 — Tower vertical, mono-bold, 4 shapes
{"version":3,"title":"Tháp đứng","paletteId":"mono-bold","shapes":[{"id":"s_0","shape":"cylinder","color":"#0a0a0a","material":"metal","motion":"still","position":[0,-1.0,0],"size":[1.2,0.4,1.2],"scale":1.2,"name":"đế"},{"id":"s_1","shape":"box","color":"#5a5a5a","material":"matte","motion":"still","position":[0,-0.2,0],"size":[0.9,0.6,0.9],"scale":1.0,"name":"thân"},{"id":"s_2","shape":"cylinder","color":"#e63946","material":"glow","motion":"pulse","position":[0,0.6,0],"size":[0.55,1.1,0.55],"scale":1.1,"name":"thân trên"},{"id":"s_3","shape":"octahedron","color":"#ffd23f","material":"metal","motion":"spin","position":[0,1.4,0],"size":[0.55,0.85,0.55],"scale":1.0,"name":"đỉnh"}],"texts":[],"aiNotes":"4 tầng stack cùng x=0, vật liệu chuyển dần metal→matte→glow→metal, đỉnh vàng spin tạo hồi quy."}

### Exemplar 4 — Mandala radial, soft-electric, 7 shapes (1 center + 6 petals)
{"version":3,"title":"Mandala dịu","paletteId":"soft-electric","shapes":[{"id":"s_0","shape":"icosahedron","color":"#8a4dff","material":"glow","motion":"pulse","position":[0,0,0],"size":[1.0,1.0,1.0],"scale":1.4,"name":"tâm"},{"id":"s_1","shape":"capsule","color":"#36d6c9","material":"matte","motion":"orbit","position":[1.5,0,0],"size":[0.5,0.9,0.5],"scale":0.9},{"id":"s_2","shape":"capsule","color":"#36d6c9","material":"matte","motion":"orbit","position":[0.75,1.30,0],"size":[0.5,0.9,0.5],"scale":0.9},{"id":"s_3","shape":"capsule","color":"#36d6c9","material":"matte","motion":"orbit","position":[-0.75,1.30,0],"size":[0.5,0.9,0.5],"scale":0.9},{"id":"s_4","shape":"capsule","color":"#36d6c9","material":"matte","motion":"orbit","position":[-1.5,0,0],"size":[0.5,0.9,0.5],"scale":0.9},{"id":"s_5","shape":"capsule","color":"#36d6c9","material":"matte","motion":"orbit","position":[-0.75,-1.30,0],"size":[0.5,0.9,0.5],"scale":0.9},{"id":"s_6","shape":"capsule","color":"#36d6c9","material":"matte","motion":"orbit","position":[0.75,-1.30,0],"size":[0.5,0.9,0.5],"scale":0.9}],"texts":[],"aiNotes":"6 petal đặt tại góc i*60°, r=1.5; tâm icosahedron glow tím; orbit motion để 6 petal trôi nhẹ quanh."}

═══════════════════════════════════════════════════════════════

Hãy SUY NGHĨ ngắn về palette + layout pattern + 1 'điểm nhấn' (hero shape lớn nhất / nổi bật nhất) trước khi viết JSON. Sau đó output đúng schema. KHÔNG viết suy nghĩ ra ngoài JSON.`
}

// buildUserPrompt sends the mode + (for polish/remix) compressed full scene.
// Random mode omits currentScene so the LLM starts from blank.
func buildUserPrompt(mode model.LLMMode, req model.LLMRequest) string {
	var sb strings.Builder
	sb.WriteString("mode: ")
	sb.WriteString(string(mode))
	sb.WriteString("\n")

	if req.CurrentScene != nil {
		sb.WriteString("currentScene: ")
		b, _ := json.Marshal(req.CurrentScene)
		sb.Write(b)
		sb.WriteString("\n")
	}
	if req.StrokeCount > 0 {
		sb.WriteString(fmt.Sprintf("userStrokeCount: %d (user-drawn freehand strokes; bạn KHÔNG được tạo strokes — preserve verbatim)\n", req.StrokeCount))
	}
	switch mode {
	case model.LLMModeRandom:
		sb.WriteString("\nTạo scene mới hoàn toàn theo brief trên. Output JSON LLMScene v3 ngay.")
	case model.LLMModePolish:
		sb.WriteString("\nPolish currentScene: GIỮ phần lớn shape id, chỉ tinh chỉnh color/material/motion/position/size cho hài hoà. Output JSON LLMScene v3 ngay.")
	case model.LLMModeRemix:
		sb.WriteString("\nRemix currentScene: TỰ DO đổi palette/layout/hero, có thể thêm/bớt shape; giữ ≥30% id nếu currentScene >4 shape; aiNotes ghi rõ đã đổi gì. Output JSON LLMScene v3 ngay.")
	}
	return sb.String()
}
