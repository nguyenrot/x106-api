package model

// LLM "art director" layer for the art studio. The Go API is the only place
// that talks to DeepSeek; the frontend never sees the API key.
//
// Schema v3 (AI-first refactor): the LLM authors a full LLMScene — every
// shape's position/size/color/material/motion is explicit. Frontend just
// sanitizes + renders.

type LLMMode string

const (
	LLMModeRandom LLMMode = "random"
	LLMModePolish LLMMode = "polish"
	LLMModeRemix  LLMMode = "remix"
)

// LLMShape is one renderable primitive. Position is bbox-clamped to
// x±2.5 / y±1.6 / z±1.0; size axes to [0.3, 4.0]; scale to [0.4, 2.4].
type LLMShape struct {
	ID       string     `json:"id"`
	Shape    string     `json:"shape"`
	Color    string     `json:"color"`
	Material string     `json:"material"`
	Motion   string     `json:"motion"`
	Position [3]float64 `json:"position"`
	Size     [3]float64 `json:"size"`
	Scale    float64    `json:"scale"`
	Rotation *[3]float64 `json:"rotation,omitempty"`
	Name     string     `json:"name,omitempty"`
}

type LLMText struct {
	ID       string      `json:"id"`
	Content  string      `json:"content"`
	Font     string      `json:"font"`
	Align    string      `json:"align"`
	Color    string      `json:"color"`
	Material string      `json:"material"`
	Motion   string      `json:"motion"`
	Position [3]float64  `json:"position"`
	Scale    float64     `json:"scale"`
	Rotation *[3]float64 `json:"rotation,omitempty"`
	Name     string      `json:"name,omitempty"`
}

type LLMScene struct {
	Version    int        `json:"version"`
	Title      string     `json:"title"`
	PaletteID  string     `json:"paletteId"`
	Background string     `json:"background,omitempty"`
	Shapes     []LLMShape `json:"shapes"`
	Texts      []LLMText  `json:"texts"`
	AINotes    string     `json:"aiNotes,omitempty"`
}

// LLMRequest is what the frontend posts on /api/v1/studio/llm/{mode}.
// CurrentScene is omitted for random mode; full compressed scene for polish/remix.
type LLMRequest struct {
	CurrentScene *LLMScene `json:"currentScene,omitempty"`
	StrokeCount  int       `json:"strokeCount,omitempty"`
}

type LLMResponse struct {
	Scene     LLMScene `json:"scene"`
	Used      int      `json:"used"`
	Remaining int      `json:"remaining"`
	Limit     int      `json:"limit"`
}

type LLMQuotaResponse struct {
	Used      int `json:"used"`
	Remaining int `json:"remaining"`
	Limit     int `json:"limit"`
}

// LLMRequestLog is the row returned by the admin list endpoint.
// Heavy fields (request_payload/response_raw/parsed_direction) are excluded
// here; fetch via the detail endpoint.
//
// Note: the DB column is still named `parsed_direction` for migration-free
// compatibility, but its JSON content is now an LLMScene.
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
	RequestPayload string `json:"requestPayload,omitempty"`
	ResponseRaw    string `json:"responseRaw,omitempty"`
	ParsedScene    string `json:"parsedScene,omitempty"`
}

type LLMRequestLogListResponse struct {
	Items  []LLMRequestLog `json:"items"`
	Total  int             `json:"total"`
	Limit  int             `json:"limit"`
	Offset int             `json:"offset"`
}

// ─── Async LLM jobs (queue) ──────────────────────────────────────────────
//
// Cloudflare's free-tier 100s proxy ceiling makes synchronous DeepSeek calls
// flaky (v4-pro reasoning + retry can exceed the budget). The async path
// submits a job, returns an id, then the client polls; the worker runs the
// actual DeepSeek call on the VPS without going through Cloudflare.

type LLMJobStatus string

const (
	LLMJobPending    LLMJobStatus = "pending"
	LLMJobProcessing LLMJobStatus = "processing"
	LLMJobDone       LLMJobStatus = "done"
	LLMJobFailed     LLMJobStatus = "failed"
	LLMJobCanceled   LLMJobStatus = "canceled"
)

type LLMJobSubmitResponse struct {
	JobID     string `json:"jobId"`
	Used      int    `json:"used"`
	Remaining int    `json:"remaining"`
	Limit     int    `json:"limit"`
}

// LLMJobStatusResponse is the polling payload. Scene only present when
// status=done; ErrorMessage only when status=failed/canceled. Quota counters
// are always returned so the client can keep its badge fresh.
type LLMJobStatusResponse struct {
	JobID        string       `json:"jobId"`
	Status       LLMJobStatus `json:"status"`
	Mode         LLMMode      `json:"mode"`
	Scene        *LLMScene    `json:"scene,omitempty"`
	ErrorMessage string       `json:"errorMessage,omitempty"`
	ElapsedMs    int          `json:"elapsedMs"`
	Used         int          `json:"used"`
	Remaining    int          `json:"remaining"`
	Limit        int          `json:"limit"`
}

// ─── Admin queue views ───────────────────────────────────────────────────

// LLMJobRow is one row in the admin job list. Heavy fields (request_body,
// result_scene) are loaded only via the detail endpoint.
type LLMJobRow struct {
	ID           string       `json:"id"`
	UserID       string       `json:"userId"`
	Username     string       `json:"username"`
	Mode         LLMMode      `json:"mode"`
	Status       LLMJobStatus `json:"status"`
	Attempt      int          `json:"attempt"`
	ErrorMessage string       `json:"errorMessage,omitempty"`
	RunMs        int          `json:"runMs"` // started_at→finished_at; 0 while in flight or pending
	CreatedAt    string       `json:"createdAt"`
	StartedAt    string       `json:"startedAt,omitempty"`
	FinishedAt   string       `json:"finishedAt,omitempty"`
}

// LLMJobDetail returns the full row including the JSON columns as strings so
// the admin UI can pretty-print them without re-validating against the live
// schema (validation may have evolved since the row was written).
type LLMJobDetail struct {
	LLMJobRow
	RequestBody string `json:"requestBody,omitempty"`
	ResultScene string `json:"resultScene,omitempty"`
}

type LLMJobListResponse struct {
	Items  []LLMJobRow `json:"items"`
	Total  int         `json:"total"`
	Limit  int         `json:"limit"`
	Offset int         `json:"offset"`
}
