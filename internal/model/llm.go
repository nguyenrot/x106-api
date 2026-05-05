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
	Kind  string  `json:"kind"`
	Color string  `json:"color"`
	Size  float64 `json:"size,omitempty"`
	X     float64 `json:"x"`
	Y     float64 `json:"y"`
	Z     float64 `json:"z,omitempty"`
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
