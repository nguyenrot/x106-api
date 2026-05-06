package service

import (
	"database/sql"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/pkn/api/internal/database"
	"github.com/pkn/api/internal/model"
)

// LLMLogInput captures everything observable about a single DeepSeek HTTP
// call (one attempt — retries log a second row). Fields that don't apply to a
// failure (parsed direction, tokens) stay at zero values.
type LLMLogInput struct {
	UserID           string
	Username         string
	Mode             string
	Model            string
	Attempt          int
	Temperature      float64
	RequestPayload   []byte
	ResponseRaw      []byte
	ParsedDirection  *model.LLMDirection
	Status           string
	ErrorMessage     string
	LatencyMs        int
	PromptTokens     int
	CompletionTokens int
	TotalTokens      int
}

// RecordLLMLog persists one attempt. Errors are non-fatal — logging must
// never block or fail an LLM call, so callers should fire-and-forget.
func RecordLLMLog(in LLMLogInput) error {
	var (
		parsedJSON []byte
		err        error
	)
	if in.ParsedDirection != nil {
		parsedJSON, err = json.Marshal(in.ParsedDirection)
		if err != nil {
			return err
		}
	}
	requestJSON := nullableBytes(in.RequestPayload)
	responseRaw := nullableString(in.ResponseRaw)
	parsedNullable := nullableBytes(parsedJSON)
	errMsg := strings.TrimSpace(in.ErrorMessage)

	_, err = database.DB.Exec(
		`INSERT INTO llm_request_logs (
			user_id, username, mode, model, attempt, temperature,
			request_payload, response_raw, parsed_direction,
			status, error_message, latency_ms,
			prompt_tokens, completion_tokens, total_tokens
		) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		in.UserID, in.Username, in.Mode, in.Model, in.Attempt, in.Temperature,
		requestJSON, responseRaw, parsedNullable,
		in.Status, errMsg, in.LatencyMs,
		in.PromptTokens, in.CompletionTokens, in.TotalTokens,
	)
	return err
}

func nullableBytes(b []byte) interface{} {
	if len(b) == 0 {
		return nil
	}
	return string(b)
}

func nullableString(b []byte) interface{} {
	if len(b) == 0 {
		return nil
	}
	return string(b)
}

// LLMLogQuery is the filter set the admin endpoint accepts.
type LLMLogQuery struct {
	UserID string
	Status string // "" | "success" | "error" prefix matches
	Mode   string
	Limit  int
	Offset int
}

// ListLLMLogs returns rows newest-first with the request_payload/response_raw
// truncated for the list view (full bodies are fetched per-row via GetLLMLog).
func ListLLMLogs(q LLMLogQuery) ([]model.LLMRequestLog, int, error) {
	limit := q.Limit
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	offset := q.Offset
	if offset < 0 {
		offset = 0
	}

	where := []string{"1=1"}
	args := []interface{}{}
	if q.UserID != "" {
		where = append(where, "user_id = ?")
		args = append(args, q.UserID)
	}
	if q.Mode != "" {
		where = append(where, "mode = ?")
		args = append(args, q.Mode)
	}
	if q.Status != "" {
		where = append(where, "status = ?")
		args = append(args, q.Status)
	}
	clause := strings.Join(where, " AND ")

	var total int
	if err := database.DB.QueryRow(
		"SELECT COUNT(*) FROM llm_request_logs WHERE "+clause, args...,
	).Scan(&total); err != nil {
		return nil, 0, err
	}

	rows, err := database.DB.Query(
		`SELECT id, user_id, username, mode, model, attempt, temperature,
		        status, error_message, latency_ms,
		        prompt_tokens, completion_tokens, total_tokens, created_at
		 FROM llm_request_logs WHERE `+clause+`
		 ORDER BY id DESC LIMIT ? OFFSET ?`,
		append(args, limit, offset)...,
	)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	out := make([]model.LLMRequestLog, 0, limit)
	for rows.Next() {
		var r model.LLMRequestLog
		var errMsg sql.NullString
		var created time.Time
		if err := rows.Scan(
			&r.ID, &r.UserID, &r.Username, &r.Mode, &r.Model,
			&r.Attempt, &r.Temperature, &r.Status, &errMsg, &r.LatencyMs,
			&r.PromptTokens, &r.CompletionTokens, &r.TotalTokens, &created,
		); err != nil {
			return nil, 0, err
		}
		if errMsg.Valid {
			r.ErrorMessage = errMsg.String
		}
		r.CreatedAt = created.UTC().Format(time.RFC3339)
		out = append(out, r)
	}
	return out, total, rows.Err()
}

// GetLLMLog returns the full row including request_payload/response_raw/
// parsed_direction for the detail panel.
func GetLLMLog(id int64) (*model.LLMRequestLogDetail, error) {
	var d model.LLMRequestLogDetail
	var (
		errMsg, requestPayload, responseRaw, parsedDir sql.NullString
		created                                        time.Time
	)
	err := database.DB.QueryRow(
		`SELECT id, user_id, username, mode, model, attempt, temperature,
		        request_payload, response_raw, parsed_direction,
		        status, error_message, latency_ms,
		        prompt_tokens, completion_tokens, total_tokens, created_at
		 FROM llm_request_logs WHERE id = ?`, id,
	).Scan(
		&d.ID, &d.UserID, &d.Username, &d.Mode, &d.Model,
		&d.Attempt, &d.Temperature,
		&requestPayload, &responseRaw, &parsedDir,
		&d.Status, &errMsg, &d.LatencyMs,
		&d.PromptTokens, &d.CompletionTokens, &d.TotalTokens, &created,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if errMsg.Valid {
		d.ErrorMessage = errMsg.String
	}
	if requestPayload.Valid {
		d.RequestPayload = requestPayload.String
	}
	if responseRaw.Valid {
		d.ResponseRaw = responseRaw.String
	}
	if parsedDir.Valid {
		d.ParsedDirection = parsedDir.String
	}
	d.CreatedAt = created.UTC().Format(time.RFC3339)
	return &d, nil
}
