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

// EffectiveDailyLimit returns the runtime daily limit — settings table override
// takes precedence over the env-based config default.
func EffectiveDailyLimit(cfg *config.Config) int {
	v, _ := GetSetting(SettingLLMDailyLimit)
	if v != "" {
		if n, err := strconv.Atoi(strings.TrimSpace(v)); err == nil && n > 0 {
			return n
		}
	}
	return cfg.LLMDailyLimit
}

// LLMEnabled returns false only if admin explicitly turned LLM off via setting.
// Default is enabled.
func LLMEnabled() bool {
	v, _ := GetSetting(SettingLLMEnabled)
	if strings.TrimSpace(v) == "off" {
		return false
	}
	return true
}

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
	llmShapeKinds = map[string]bool{
		"sphere": true, "box": true, "torus": true, "knot": true,
		"panel": true, "cone": true, "cylinder": true, "capsule": true,
		"icosahedron": true, "octahedron": true, "disc": true,
	}
	llmHarmonyRules = map[string]bool{
		"alternate": true, "gradient": true, "hero-only": true, "spectrum": true,
	}
	llmExotics = map[string]bool{
		"mono-glow": true, "giant-solo": true, "deep-cluster": true,
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
	if !LLMEnabled() {
		return model.LLMDirection{}, 0, 0, ErrLLMOff
	}

	limit := EffectiveDailyLimit(cfg)
	_, remaining, err := GetQuota(userID, limit)
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
	r := limit - newCount
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

var deepseekHTTPClient = &http.Client{Timeout: 28 * time.Second}

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
		// v4-flash is a reasoning model — most output tokens are spent on
		// internal reasoning, so the budget must be generous to leave room
		// for the JSON answer itself.
		MaxTokens:   2048,
		Temperature: temperature,
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
		log.Printf("[llm] http error: %v", err)
		return model.LLMDirection{}, fmt.Errorf("%w: %v", ErrLLMUpstream, err)
	}
	defer resp.Body.Close()

	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		log.Printf("[llm] upstream %d: %s", resp.StatusCode, truncateForLog(raw))
		return model.LLMDirection{}, fmt.Errorf("%w: status=%d", ErrLLMUpstream, resp.StatusCode)
	}

	var ds deepseekResponse
	if err := json.Unmarshal(raw, &ds); err != nil {
		log.Printf("[llm] parse top-level: %v body=%s", err, truncateForLog(raw))
		return model.LLMDirection{}, fmt.Errorf("%w: parse: %v", ErrLLMUpstream, err)
	}
	if ds.Error != nil {
		log.Printf("[llm] upstream error: %s", ds.Error.Message)
		return model.LLMDirection{}, fmt.Errorf("%w: %s", ErrLLMUpstream, ds.Error.Message)
	}
	if len(ds.Choices) == 0 || strings.TrimSpace(ds.Choices[0].Message.Content) == "" {
		log.Printf("[llm] empty content: %s", truncateForLog(raw))
		return model.LLMDirection{}, fmt.Errorf("%w: empty content", ErrLLMUpstream)
	}

	content := ds.Choices[0].Message.Content
	var dir model.LLMDirection
	if err := json.Unmarshal([]byte(content), &dir); err != nil {
		log.Printf("[llm] invalid direction json: %v content=%s", err, truncateForLog([]byte(content)))
		return model.LLMDirection{}, fmt.Errorf("%w: invalid direction json: %v", ErrLLMUpstream, err)
	}
	if err := validateAndClampDirection(&dir); err != nil {
		log.Printf("[llm] direction validation failed: %v content=%s", err, truncateForLog([]byte(content)))
		return model.LLMDirection{}, fmt.Errorf("%w: %v", ErrLLMUpstream, err)
	}
	return dir, nil
}

func truncateForLog(b []byte) string {
	const max = 600
	if len(b) <= max {
		return string(b)
	}
	return string(b[:max]) + "…"
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

	// shapeCount: clamp to 0..100 (0 = engine default).
	// 100 is a hard ceiling — frontend pad logic uses a golden-angle spiral
	// to spread up to that many shapes without clumping.
	if d.ShapeCount < 0 {
		d.ShapeCount = 0
	} else if d.ShapeCount > 100 {
		d.ShapeCount = 100
	}

	// shapeBias: keep only valid kinds, dedupe
	if len(d.ShapeBias) > 0 {
		seen := map[string]bool{}
		filtered := d.ShapeBias[:0]
		for _, k := range d.ShapeBias {
			if llmShapeKinds[k] && !seen[k] {
				seen[k] = true
				filtered = append(filtered, k)
			}
		}
		d.ShapeBias = filtered
	}

	// harmonyRule: drop unknown
	if d.HarmonyRule != "" && !llmHarmonyRules[d.HarmonyRule] {
		d.HarmonyRule = ""
	}

	// exotic: drop unknown
	if d.Exotic != "" && !llmExotics[d.Exotic] {
		d.Exotic = ""
	}

	// heroes: cap to 3, validate kind, clamp positions and per-axis W/H/D
	if len(d.Heroes) > 0 {
		if len(d.Heroes) > 3 {
			d.Heroes = d.Heroes[:3]
		}
		filtered := d.Heroes[:0]
		for _, h := range d.Heroes {
			if !llmShapeKinds[h.Kind] {
				continue
			}
			if h.Size <= 0 {
				h.Size = 1.0
			}
			h.Size = clampFloat(h.Size, 0.4, 1.8)
			// Per-axis dimensions: AI may specify width/height/depth to break
			// the [1,1,1] uniform default. If any axis is unset (<=0), fall
			// back to Size for that axis so legacy responses stay valid.
			if h.Width <= 0 {
				h.Width = h.Size
			}
			if h.Height <= 0 {
				h.Height = h.Size
			}
			if h.Depth <= 0 {
				h.Depth = h.Size
			}
			h.Width = clampFloat(h.Width, 0.3, 4.0)
			h.Height = clampFloat(h.Height, 0.3, 4.0)
			h.Depth = clampFloat(h.Depth, 0.3, 4.0)
			h.X = clampFloat(h.X, -2.5, 2.5)
			h.Y = clampFloat(h.Y, -1.6, 1.6)
			h.Z = clampFloat(h.Z, -1.0, 1.0)
			filtered = append(filtered, h)
		}
		d.Heroes = filtered
	}

	// sizeRanges: per-axis [min, max] applied to engine-generated (non-hero)
	// shapes. If only min or max is set, mirror to the other side. Range is
	// auto-swapped if AI sent min>max. Drop the field entirely if invalid.
	if d.SizeRanges != nil {
		sr := d.SizeRanges
		clampAxis := func(lo, hi *float64) {
			if *lo <= 0 && *hi <= 0 {
				return
			}
			if *lo <= 0 {
				*lo = *hi
			}
			if *hi <= 0 {
				*hi = *lo
			}
			if *lo > *hi {
				*lo, *hi = *hi, *lo
			}
			*lo = clampFloat(*lo, 0.3, 4.0)
			*hi = clampFloat(*hi, 0.3, 4.0)
		}
		clampAxis(&sr.WidthMin, &sr.WidthMax)
		clampAxis(&sr.HeightMin, &sr.HeightMax)
		clampAxis(&sr.DepthMin, &sr.DepthMax)
		// If everything is zero, drop the struct so the engine uses defaults.
		if sr.WidthMin == 0 && sr.WidthMax == 0 &&
			sr.HeightMin == 0 && sr.HeightMax == 0 &&
			sr.DepthMin == 0 && sr.DepthMax == 0 {
			d.SizeRanges = nil
		}
	}
	return nil
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

func DefaultSystemPrompt() string {
	return `Bạn là "art director" cho một studio nghệ thuật 3D minimal trên giấy (paper-tone aesthetic). Mỗi lần được gọi, bạn trả VỀ JSON đúng schema dưới đây để hướng dẫn engine sinh layout. Engine xử lý spacing/relax/animation — bạn chọn hướng thẩm mỹ + đặt 1-3 "hero shape" cốt lõi.

OUTPUT: chỉ JSON object, không markdown, không lời dẫn. Schema:
{
  "paletteId": <poster-bright|museum-pop|soft-electric|forest-calm|sunset-coral|ocean-mist|pastel-garden|mono-bold|tropical-punch|vintage-press>,
  "compositionId": <row|ring|tower|constellation|mirror|solo-hero|wave|vortex|mandala|cascade|horizon|petal>,
  "materialMood": <glow-heavy|metal-heavy|matte-heavy|glass-heavy|balanced>,
  "motionMood": <still|drifting|spinning|pulsing|orbital>,
  "title": <tiếng Việt, ≤ 40 ký tự, có thể dùng "·">,
  "textPhrase": <tiếng Việt, ≤ 60 ký tự; câu thơ/thì thầm; có thể "">,
  "shapeCount": <số shape engine dùng, 4-100; 0 = engine tự chọn. Mặc định 6-12. Đông (>30) chỉ dùng cho composition constellation/mandala/wave/vortex>,
  "shapeBias": <mảng kinds engine ưu tiên khi cần thêm shape; chọn từ: sphere, box, torus, knot, panel, cone, cylinder, capsule, icosahedron, octahedron, disc>,
  "harmonyRule": <alternate|gradient|hero-only|spectrum|""> // alternate=so le 2 màu, gradient=lan tỏa, hero-only=1 màu chính, spectrum=trải đều
  "exotic": <mono-glow|giant-solo|deep-cluster|""> // hiệu ứng mạnh: mono-glow=tất cả phát sáng cùng màu, giant-solo=1 shape khổng lồ, deep-cluster=cụm sát nhau
  "heroes": [ // 1-3 shape NỀN do bạn quyết định vị trí; engine bố cục các shape phụ quanh chúng. Có thể bỏ trống.
    {
      "kind": <sphere|box|torus|knot|panel|cone|cylinder|capsule|icosahedron|octahedron|disc>,
      "color": <hex màu nằm trong palette đã chọn, vd "#f03248">,
      "width": <BẮT BUỘC, 0.3-4.0, kích thước trục X — KHÔNG dùng giá trị 1.0 mặc định, hãy chọn có chủ đích>,
      "height": <BẮT BUỘC, 0.3-4.0, kích thước trục Y — KHÔNG dùng giá trị 1.0 mặc định, hãy chọn có chủ đích>,
      "depth": <BẮT BUỘC, 0.3-4.0, kích thước trục Z — KHÔNG dùng giá trị 1.0 mặc định, hãy chọn có chủ đích>,
      "x": <-2.5..2.5>,
      "y": <-1.6..1.6>,
      "z": <-1..1>
    }
  ],
  "sizeRanges": { // BẮT BUỘC trả về, áp cho TẤT CẢ shape engine sinh (không phải hero). Bạn quyết toàn bộ phân phối kích thước scene.
    "widthMin": <BẮT BUỘC, 0.3-4.0>, "widthMax": <BẮT BUỘC, 0.3-4.0>,
    "heightMin": <BẮT BUỘC, 0.3-4.0>, "heightMax": <BẮT BUỘC, 0.3-4.0>,
    "depthMin": <BẮT BUỘC, 0.3-4.0>, "depthMax": <BẮT BUỘC, 0.3-4.0>
  }
}

VÍ DỤ 1 (hero là cột tháp đứng, panel ngang phụ):
{"paletteId":"sunset-coral","compositionId":"solo-hero","materialMood":"glow-heavy","motionMood":"pulsing","title":"Đoá rực giữa lặng","textPhrase":"khẽ thở · sáng dần","shapeCount":7,"shapeBias":["torus","disc","sphere"],"harmonyRule":"hero-only","exotic":"giant-solo","heroes":[{"kind":"cylinder","color":"#ff6b6b","width":0.9,"height":2.6,"depth":0.9,"x":0,"y":0.2,"z":0}],"sizeRanges":{"widthMin":0.6,"widthMax":1.4,"heightMin":0.5,"heightMax":1.6,"depthMin":0.5,"depthMax":1.2}}

VÍ DỤ 2 (count cao, đĩa mỏng phẳng, hero là panel ngang dài):
{"paletteId":"ocean-mist","compositionId":"constellation","materialMood":"glow-heavy","motionMood":"drifting","title":"Vô số ánh","textPhrase":"thiên hà thì thầm","shapeCount":60,"shapeBias":["sphere","disc","icosahedron"],"harmonyRule":"gradient","exotic":"mono-glow","heroes":[{"kind":"panel","color":"#0d6e8c","width":3.2,"height":0.6,"depth":0.4,"x":0,"y":-0.3,"z":0}],"sizeRanges":{"widthMin":0.4,"widthMax":1.1,"heightMin":0.4,"heightMax":1.1,"depthMin":0.3,"depthMax":0.5}}

VÍ DỤ 3 (hero hình đĩa dẹt, scene loạn nhịp):
{"paletteId":"mono-bold","compositionId":"vortex","materialMood":"metal-heavy","motionMood":"orbital","title":"Xoáy trầm","textPhrase":"vọng âm · vô định","shapeCount":24,"shapeBias":["box","panel","octahedron"],"harmonyRule":"alternate","heroes":[{"kind":"disc","color":"#101820","width":2.8,"height":2.8,"depth":0.3,"x":0,"y":0,"z":0}],"sizeRanges":{"widthMin":0.5,"widthMax":2.2,"heightMin":0.4,"heightMax":1.8,"depthMin":0.4,"depthMax":1.6}}

Quy tắc:
- Đa dạng giữa các lần gọi; tránh trùng paletteId/compositionId trong "previous".
- Polish: thay đổi nhẹ, giữ tinh thần; ít heroes, có thể bỏ exotic.
- Remix: dám đổi palette + composition + dùng exotic + 2-3 heroes.
- exotic chỉ chọn ~30% lần gọi (không lần nào cũng có).
- KHÔNG dùng exotic "giant-solo" hoặc "deep-cluster" khi shapeCount > 20 — engine sẽ pad thêm shape làm lệch ý đồ exotic. Chỉ "mono-glow" tương thích với count cao.
- "title" tiếng Việt, chất thơ.
- "color" trong heroes BẮT BUỘC nằm trong các swatch của palette đã chọn.
- KÍCH THƯỚC (CỰC KỲ QUAN TRỌNG): bạn là người DUY NHẤT quyết định kích thước. NGHIÊM CẤM mọi giá trị mặc định 1.0, 1, hay W=H=D đồng đều thiếu chủ đích. MỖI hero PHẢI có 3 trục width/height/depth khác nhau hoặc tỉ lệ rõ rệt (cột tháp height ≥ 2× width; panel ngang width ≥ 2× height; đĩa dẹt depth ≤ 0.5; cầu cân đối thì width≈height≈depth nhưng phải ở giá trị có chủ đích như 1.6 hoặc 2.4, KHÔNG phải 1.0).
- "sizeRanges" BẮT BUỘC LUÔN trả về với đủ 6 field min/max. Mood của bạn lái distribution: scene tĩnh đồng đều thì min≈max nhưng KHÔNG phải 1.0; scene loạn nhịp thì khoảng rộng (vd width 0.4→2.4). Mỗi trục có thể có range khác nhau để tạo silhouette tổng thể (vd height range hẹp + width range rộng = scene "trải ngang").
- Đa dạng giữa các lần gọi cũng áp cho kích thước: tránh lặp lại cùng range của lần trước.`
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
