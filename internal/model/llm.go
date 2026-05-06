package model

// LLM "director" layer for the art studio. The Go API is the only place that
// talks to DeepSeek; the frontend never sees the API key.

type LLMMode string

const (
	LLMModeRandom LLMMode = "random"
	LLMModePolish LLMMode = "polish"
	LLMModeRemix  LLMMode = "remix"
)

type LLMSceneSummary struct {
	PaletteID     string   `json:"paletteId"`
	CompositionID string   `json:"compositionId,omitempty"`
	ShapeCount    int      `json:"shapeCount"`
	Materials     []string `json:"materials,omitempty"`
	Motions       []string `json:"motions,omitempty"`
	HasText       bool     `json:"hasText"`
	Title         string   `json:"title,omitempty"`
}

type LLMPreviousHint struct {
	PaletteID     string `json:"paletteId,omitempty"`
	CompositionID string `json:"compositionId,omitempty"`
	TextPhrase    string `json:"textPhrase,omitempty"`
}

type LLMRequest struct {
	Scene    *LLMSceneSummary `json:"scene,omitempty"`
	Previous *LLMPreviousHint `json:"previous,omitempty"`
}

type LLMHeroShape struct {
	Kind   string  `json:"kind"`
	Color  string  `json:"color"`
	Size   float64 `json:"size,omitempty"`
	Width  float64 `json:"width,omitempty"`
	Height float64 `json:"height,omitempty"`
	Depth  float64 `json:"depth,omitempty"`
	X      float64 `json:"x"`
	Y      float64 `json:"y"`
	Z      float64 `json:"z,omitempty"`
}

// LLMSizeRanges lets the director set per-axis W/H/D ranges for the
// non-hero shapes the engine generates. Each axis is sampled uniformly
// within [min, max]. Missing ranges fall back to engine defaults.
type LLMSizeRanges struct {
	WidthMin  float64 `json:"widthMin,omitempty"`
	WidthMax  float64 `json:"widthMax,omitempty"`
	HeightMin float64 `json:"heightMin,omitempty"`
	HeightMax float64 `json:"heightMax,omitempty"`
	DepthMin  float64 `json:"depthMin,omitempty"`
	DepthMax  float64 `json:"depthMax,omitempty"`
}

type LLMDirection struct {
	PaletteID     string         `json:"paletteId"`
	CompositionID string         `json:"compositionId"`
	MaterialMood  string         `json:"materialMood"`
	MotionMood    string         `json:"motionMood"`
	Title         string         `json:"title"`
	TextPhrase    string         `json:"textPhrase,omitempty"`
	ShapeCount    int            `json:"shapeCount,omitempty"`
	ShapeBias     []string       `json:"shapeBias,omitempty"`
	HarmonyRule   string         `json:"harmonyRule,omitempty"`
	Exotic        string         `json:"exotic,omitempty"`
	Heroes        []LLMHeroShape `json:"heroes,omitempty"`
	SizeRanges    *LLMSizeRanges `json:"sizeRanges,omitempty"`
}

type LLMResponse struct {
	Direction LLMDirection `json:"direction"`
	Used      int          `json:"used"`
	Remaining int          `json:"remaining"`
	Limit     int          `json:"limit"`
}

type LLMQuotaResponse struct {
	Used      int `json:"used"`
	Remaining int `json:"remaining"`
	Limit     int `json:"limit"`
}

// LLMRequestLog is the row returned by the admin list endpoint.
// Heavy fields (request_payload/response_raw/parsed_direction) are excluded
// here; fetch via the detail endpoint.
type LLMRequestLog struct {
	ID               int64   `json:"id"`
	UserID           string  `json:"userId"`
	Username         string  `json:"username"`
	Mode             string  `json:"mode"`
	Model            string  `json:"model"`
	Attempt          int     `json:"attempt"`
	Temperature      float64 `json:"temperature"`
	Status           string  `json:"status"`
	ErrorMessage     string  `json:"errorMessage,omitempty"`
	LatencyMs        int     `json:"latencyMs"`
	PromptTokens     int     `json:"promptTokens"`
	CompletionTokens int     `json:"completionTokens"`
	TotalTokens      int     `json:"totalTokens"`
	CreatedAt        string  `json:"createdAt"`
}

type LLMRequestLogDetail struct {
	LLMRequestLog
	RequestPayload  string `json:"requestPayload,omitempty"`
	ResponseRaw     string `json:"responseRaw,omitempty"`
	ParsedDirection string `json:"parsedDirection,omitempty"`
}

type LLMRequestLogListResponse struct {
	Items  []LLMRequestLog `json:"items"`
	Total  int             `json:"total"`
	Limit  int             `json:"limit"`
	Offset int             `json:"offset"`
}
