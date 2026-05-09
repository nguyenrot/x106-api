package worker

import (
	"context"
	"errors"
	"log"
	"time"

	"github.com/pkn/api/internal/config"
	"github.com/pkn/api/internal/model"
	"github.com/pkn/api/internal/service"
)

// JobTimeout caps how long a single DeepSeek call can take inside the worker.
// 220s gives a 50s buffer over the deepseek HTTP client (170s) so a single
// attempt that runs to its own timeout still has room to flush the error log
// + record the row. Worker isn't fronted by Cloudflare so we're not tied to
// the 100s proxy ceiling. Dense-default prompt (50–80 shapes) pushes v4-pro
// wall time to 100–150s typically.
const JobTimeout = 220 * time.Second

// StaleJobAge: a "processing" row older than this is assumed orphaned (worker
// crashed mid-job, OS killed the process, etc) and gets recovered on every
// recovery tick. Slightly larger than JobTimeout so we don't race with a slow
// but still-alive worker.
const StaleJobAge = 280 * time.Second

// Run drives the polling loop. Returns only when ctx is canceled. Each
// iteration: try to claim a pending job, run it, mark the outcome. When the
// queue is empty, sleep `pollInterval` then try again. On a slower cadence
// (`recoveryInterval`) we sweep stale rows + delete old terminal rows.
func Run(ctx context.Context, cfg *config.Config) {
	const (
		pollInterval     = 1 * time.Second
		recoveryInterval = 60 * time.Second
		cleanupInterval  = 30 * time.Minute
		cleanupMaxAge    = 24 * time.Hour
	)

	log.Printf("[worker] started — polling every %s", pollInterval)

	// Recovery + cleanup tickers run independently of the poll loop so a flood
	// of jobs can't starve them.
	recoveryTicker := time.NewTicker(recoveryInterval)
	defer recoveryTicker.Stop()
	cleanupTicker := time.NewTicker(cleanupInterval)
	defer cleanupTicker.Stop()

	// One immediate recovery sweep at startup — on every restart we may have
	// orphaned in-flight rows.
	if n, err := service.RecoverStaleJobs(StaleJobAge); err != nil {
		log.Printf("[worker] startup recovery error: %v", err)
	} else if n > 0 {
		log.Printf("[worker] startup recovered %d stale jobs", n)
	}

	pollTimer := time.NewTimer(0)
	defer pollTimer.Stop()

	for {
		select {
		case <-ctx.Done():
			log.Printf("[worker] shutdown signal received, exiting")
			return
		case <-recoveryTicker.C:
			if n, err := service.RecoverStaleJobs(StaleJobAge); err != nil {
				log.Printf("[worker] recovery error: %v", err)
			} else if n > 0 {
				log.Printf("[worker] recovered %d stale jobs", n)
			}
		case <-cleanupTicker.C:
			if n, err := service.CleanupOldLLMJobs(cleanupMaxAge); err != nil {
				log.Printf("[worker] cleanup error: %v", err)
			} else if n > 0 {
				log.Printf("[worker] cleaned %d old jobs", n)
			}
		case <-pollTimer.C:
			workDone := processOne(ctx, cfg)
			// Empty queue → wait pollInterval. Found work → check immediately
			// (queue may still have backlog).
			if workDone {
				pollTimer.Reset(0)
			} else {
				pollTimer.Reset(pollInterval)
			}
		}
	}
}

// processOne claims one job and runs it. Returns true if a job was processed
// (regardless of success), false when the queue was empty.
func processOne(parent context.Context, cfg *config.Config) bool {
	job, err := service.ClaimNextPendingJob()
	if errors.Is(err, service.ErrJobNotFound) {
		return false
	}
	if err != nil {
		log.Printf("[worker] claim error: %v", err)
		return false
	}

	req := safeRequest(job.RequestBody)
	log.Printf("[worker] start job=%s mode=%s user=%s attempt=%d",
		job.ID, job.Mode, job.UserID, job.Attempt)
	start := time.Now()

	jobCtx, cancel := context.WithTimeout(parent, JobTimeout)
	scene, runErr := service.RunLLMJob(jobCtx, cfg, job.UserID, job.Username, job.Mode, req)
	cancel()
	elapsed := time.Since(start)

	if runErr != nil {
		log.Printf("[worker] fail job=%s elapsed=%s err=%v", job.ID, elapsed, runErr)
		if mErr := service.MarkJobFailed(job.ID, runErr.Error()); mErr != nil {
			log.Printf("[worker] mark failed error: %v", mErr)
		}
		if rErr := service.RefundLLMQuota(job.UserID); rErr != nil {
			log.Printf("[worker] refund quota error: %v", rErr)
		}
		return true
	}

	if mErr := service.MarkJobDone(job.ID, scene); mErr != nil {
		log.Printf("[worker] mark done error: %v", mErr)
		// Persisting the result failed but the DeepSeek call already succeeded
		// — refund quota so the user isn't charged for a result they'll never
		// see, and let the recovery sweep mark this row failed.
		_ = service.RefundLLMQuota(job.UserID)
		return true
	}
	log.Printf("[worker] done job=%s elapsed=%s shapes=%d",
		job.ID, elapsed, len(scene.Shapes))
	return true
}

// safeRequest unwraps the optional request body. A worker should always have
// a non-nil request (CreateLLMJob marshals the struct on insert), but a
// hand-edited row could have NULL — return a zero-value request rather than
// panic on nil-deref.
func safeRequest(r *model.LLMRequest) model.LLMRequest {
	if r == nil {
		return model.LLMRequest{}
	}
	return *r
}
