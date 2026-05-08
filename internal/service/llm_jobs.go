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

var (
	ErrJobNotFound      = errors.New("job not found")
	ErrJobInvalidStatus = errors.New("job in invalid status for this action")
)

// LLMJob mirrors a row in the llm_jobs table. RequestBody and ResultScene are
// the unmarshaled JSON columns; nil when absent.
type LLMJob struct {
	ID           string
	UserID       string
	Username     string
	Mode         model.LLMMode
	Status       model.LLMJobStatus
	RequestBody  *model.LLMRequest
	ResultScene  *model.LLMScene
	ErrorMessage string
	Attempt      int
	CreatedAt    time.Time
	StartedAt    *time.Time
	FinishedAt   *time.Time
}

// CreateLLMJob inserts a fresh pending row. The caller is responsible for
// having already reserved a quota slot via ReserveLLMQuota.
func CreateLLMJob(userID, username string, mode model.LLMMode, req model.LLMRequest) (string, error) {
	id := newID()
	body, err := json.Marshal(req)
	if err != nil {
		return "", err
	}
	_, err = database.DB.Exec(
		`INSERT INTO llm_jobs (id, user_id, username, mode, status, request_body)
		 VALUES (?, ?, ?, ?, 'pending', ?)`,
		id, userID, username, string(mode), string(body),
	)
	if err != nil {
		return "", err
	}
	return id, nil
}

// GetLLMJob fetches one job. When userID is non-empty it must match — the
// public endpoint passes the auth user, the worker passes "".
func GetLLMJob(jobID, userID string) (*LLMJob, error) {
	q := `SELECT id, user_id, username, mode, status, request_body, result_scene,
	             error_message, attempt, created_at, started_at, finished_at
	      FROM llm_jobs WHERE id = ?`
	args := []any{jobID}
	if userID != "" {
		q += " AND user_id = ?"
		args = append(args, userID)
	}
	q += " LIMIT 1"

	row := database.DB.QueryRow(q, args...)
	job, err := scanLLMJob(row)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrJobNotFound
	}
	if err != nil {
		return nil, err
	}
	return &job, nil
}

// ClaimNextPendingJob picks one pending job in FIFO order, marks it
// processing, and returns it. Returns ErrJobNotFound when the queue is empty.
//
// Uses SELECT ... FOR UPDATE SKIP LOCKED so multiple worker processes can
// safely run side-by-side. The transaction wraps SELECT + UPDATE so we never
// hand the same job to two workers.
func ClaimNextPendingJob() (*LLMJob, error) {
	tx, err := database.DB.Begin()
	if err != nil {
		return nil, err
	}
	defer tx.Rollback() //nolint:errcheck

	row := tx.QueryRow(
		`SELECT id FROM llm_jobs
		 WHERE status = 'pending'
		 ORDER BY created_at ASC
		 LIMIT 1
		 FOR UPDATE SKIP LOCKED`,
	)
	var id string
	if err := row.Scan(&id); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrJobNotFound
		}
		return nil, err
	}

	if _, err := tx.Exec(
		`UPDATE llm_jobs
		 SET status = 'processing',
		     started_at = CURRENT_TIMESTAMP(3),
		     attempt = attempt + 1
		 WHERE id = ?`,
		id,
	); err != nil {
		return nil, err
	}
	if err := tx.Commit(); err != nil {
		return nil, err
	}

	return GetLLMJob(id, "")
}

// MarkJobDone stores the rendered scene and flips status. ResultScene is
// JSON-encoded; the row preserves request_body for auditing.
func MarkJobDone(jobID string, scene model.LLMScene) error {
	body, err := json.Marshal(scene)
	if err != nil {
		return err
	}
	_, err = database.DB.Exec(
		`UPDATE llm_jobs
		 SET status = 'done',
		     result_scene = ?,
		     finished_at = CURRENT_TIMESTAMP(3),
		     error_message = NULL
		 WHERE id = ?`,
		string(body), jobID,
	)
	return err
}

// MarkJobFailed records a terminal error. The error string is truncated to
// the column width so a very chatty upstream message can't break the insert.
func MarkJobFailed(jobID, errMsg string) error {
	_, err := database.DB.Exec(
		`UPDATE llm_jobs
		 SET status = 'failed',
		     error_message = ?,
		     finished_at = CURRENT_TIMESTAMP(3)
		 WHERE id = ?`,
		clampString(errMsg, 500), jobID,
	)
	return err
}

// CancelLLMJob transitions pending → canceled (refunding quota) or
// processing → canceled (no refund — DeepSeek call already in flight). Returns
// (refunded, currentStatus, error). Idempotent for terminal statuses (returns
// ErrJobInvalidStatus so the caller can show "already done" UX).
func CancelLLMJob(jobID, userID string) (refunded bool, finalStatus model.LLMJobStatus, err error) {
	job, err := GetLLMJob(jobID, userID)
	if err != nil {
		return false, "", err
	}
	switch job.Status {
	case model.LLMJobPending:
		_, err = database.DB.Exec(
			`UPDATE llm_jobs
			 SET status = 'canceled', finished_at = CURRENT_TIMESTAMP(3)
			 WHERE id = ? AND status = 'pending'`,
			jobID,
		)
		if err != nil {
			return false, job.Status, err
		}
		// Best-effort refund. If it fails the user just loses 1 quota slot —
		// not fatal. We log but don't surface.
		if rerr := RefundLLMQuota(job.UserID); rerr != nil {
			// Soft-fail; caller doesn't need to know.
			_ = rerr
		}
		return true, model.LLMJobCanceled, nil
	case model.LLMJobProcessing:
		_, err = database.DB.Exec(
			`UPDATE llm_jobs
			 SET status = 'canceled', finished_at = CURRENT_TIMESTAMP(3)
			 WHERE id = ? AND status = 'processing'`,
			jobID,
		)
		if err != nil {
			return false, job.Status, err
		}
		return false, model.LLMJobCanceled, nil
	default:
		return false, job.Status, ErrJobInvalidStatus
	}
}

// RecoverStaleJobs finds rows stuck in processing past the deadline and marks
// them failed + refunds quota. Returns the count of recovered jobs. Called by
// the worker on startup and on a slow timer to clean up after crashes.
func RecoverStaleJobs(maxAge time.Duration) (int, error) {
	cutoff := time.Now().Add(-maxAge)
	rows, err := database.DB.Query(
		`SELECT id, user_id FROM llm_jobs
		 WHERE status = 'processing' AND started_at < ?`,
		cutoff,
	)
	if err != nil {
		return 0, err
	}
	type stale struct{ id, uid string }
	var staleList []stale
	for rows.Next() {
		var s stale
		if err := rows.Scan(&s.id, &s.uid); err != nil {
			rows.Close()
			return 0, err
		}
		staleList = append(staleList, s)
	}
	rows.Close()

	count := 0
	for _, s := range staleList {
		if err := MarkJobFailed(s.id, "worker timeout / crash recovery"); err != nil {
			continue
		}
		_ = RefundLLMQuota(s.uid)
		count++
	}
	return count, nil
}

// ─── Admin query API ─────────────────────────────────────────────────────

// LLMJobAdminQuery filters an admin list of jobs. Empty fields mean "any".
type LLMJobAdminQuery struct {
	UserID string
	Mode   string
	Status string
	Limit  int
	Offset int
}

// ListLLMJobsAdmin returns a page of jobs matching the filter, newest first,
// without the heavy JSON columns. Total is the unpaginated count.
func ListLLMJobsAdmin(q LLMJobAdminQuery) ([]model.LLMJobRow, int, error) {
	limit := q.Limit
	if limit <= 0 || limit > 200 {
		limit = 50
	}
	offset := q.Offset
	if offset < 0 {
		offset = 0
	}

	where := []string{"1=1"}
	args := []any{}
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
		"SELECT COUNT(*) FROM llm_jobs WHERE "+clause, args...,
	).Scan(&total); err != nil {
		return nil, 0, err
	}

	rows, err := database.DB.Query(
		`SELECT id, user_id, username, mode, status, attempt, error_message,
		        created_at, started_at, finished_at
		 FROM llm_jobs WHERE `+clause+`
		 ORDER BY created_at DESC LIMIT ? OFFSET ?`,
		append(args, limit, offset)...,
	)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	out := make([]model.LLMJobRow, 0, limit)
	for rows.Next() {
		var r model.LLMJobRow
		var (
			mode, status string
			errMsg       sql.NullString
			createdAt    time.Time
			startedAt    sql.NullTime
			finishedAt   sql.NullTime
		)
		if err := rows.Scan(
			&r.ID, &r.UserID, &r.Username, &mode, &status,
			&r.Attempt, &errMsg, &createdAt, &startedAt, &finishedAt,
		); err != nil {
			return nil, 0, err
		}
		r.Mode = model.LLMMode(mode)
		r.Status = model.LLMJobStatus(status)
		if errMsg.Valid {
			r.ErrorMessage = errMsg.String
		}
		r.CreatedAt = createdAt.UTC().Format(time.RFC3339)
		if startedAt.Valid {
			r.StartedAt = startedAt.Time.UTC().Format(time.RFC3339)
		}
		if finishedAt.Valid {
			r.FinishedAt = finishedAt.Time.UTC().Format(time.RFC3339)
			if startedAt.Valid {
				r.RunMs = int(finishedAt.Time.Sub(startedAt.Time) / time.Millisecond)
			}
		}
		out = append(out, r)
	}
	return out, total, rows.Err()
}

// GetLLMJobDetailAdmin returns the full row including JSON columns as raw
// strings. Returns nil when not found (no error).
func GetLLMJobDetailAdmin(jobID string) (*model.LLMJobDetail, error) {
	var d model.LLMJobDetail
	var (
		mode, status                       string
		requestBody, resultScene, errMsg   sql.NullString
		createdAt                          time.Time
		startedAt, finishedAt              sql.NullTime
	)
	err := database.DB.QueryRow(
		`SELECT id, user_id, username, mode, status, attempt, error_message,
		        request_body, result_scene,
		        created_at, started_at, finished_at
		 FROM llm_jobs WHERE id = ?`,
		jobID,
	).Scan(
		&d.ID, &d.UserID, &d.Username, &mode, &status, &d.Attempt, &errMsg,
		&requestBody, &resultScene,
		&createdAt, &startedAt, &finishedAt,
	)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	d.Mode = model.LLMMode(mode)
	d.Status = model.LLMJobStatus(status)
	if errMsg.Valid {
		d.ErrorMessage = errMsg.String
	}
	if requestBody.Valid {
		d.RequestBody = requestBody.String
	}
	if resultScene.Valid {
		d.ResultScene = resultScene.String
	}
	d.CreatedAt = createdAt.UTC().Format(time.RFC3339)
	if startedAt.Valid {
		d.StartedAt = startedAt.Time.UTC().Format(time.RFC3339)
	}
	if finishedAt.Valid {
		d.FinishedAt = finishedAt.Time.UTC().Format(time.RFC3339)
		if startedAt.Valid {
			d.RunMs = int(finishedAt.Time.Sub(startedAt.Time) / time.Millisecond)
		}
	}
	return &d, nil
}

// CleanupOldLLMJobs deletes terminal rows older than maxAge. Keeps the table
// from growing without bound; logs/admin queries can use llm_request_logs
// for historical detail.
func CleanupOldLLMJobs(maxAge time.Duration) (int64, error) {
	cutoff := time.Now().Add(-maxAge)
	res, err := database.DB.Exec(
		`DELETE FROM llm_jobs
		 WHERE status IN ('done', 'failed', 'canceled')
		   AND finished_at IS NOT NULL
		   AND finished_at < ?`,
		cutoff,
	)
	if err != nil {
		return 0, err
	}
	return res.RowsAffected()
}

// ─── helpers ─────────────────────────────────────────────────────────────

type llmJobScanner interface {
	Scan(dest ...any) error
}

func scanLLMJob(s llmJobScanner) (LLMJob, error) {
	var job LLMJob
	var (
		mode, status                   string
		requestBody, resultScene       sql.NullString
		errMsg                         sql.NullString
		startedAt, finishedAt          sql.NullTime
	)
	err := s.Scan(
		&job.ID, &job.UserID, &job.Username, &mode, &status,
		&requestBody, &resultScene, &errMsg, &job.Attempt,
		&job.CreatedAt, &startedAt, &finishedAt,
	)
	if err != nil {
		return job, err
	}
	job.Mode = model.LLMMode(mode)
	job.Status = model.LLMJobStatus(status)
	if errMsg.Valid {
		job.ErrorMessage = errMsg.String
	}
	if startedAt.Valid {
		t := startedAt.Time
		job.StartedAt = &t
	}
	if finishedAt.Valid {
		t := finishedAt.Time
		job.FinishedAt = &t
	}
	if requestBody.Valid && requestBody.String != "" {
		var req model.LLMRequest
		if err := json.Unmarshal([]byte(requestBody.String), &req); err == nil {
			job.RequestBody = &req
		}
	}
	if resultScene.Valid && resultScene.String != "" {
		var sc model.LLMScene
		if err := json.Unmarshal([]byte(resultScene.String), &sc); err == nil {
			job.ResultScene = &sc
		}
	}
	return job, nil
}

func clampString(s string, max int) string {
	s = strings.TrimSpace(s)
	if len(s) <= max {
		return s
	}
	return s[:max]
}
