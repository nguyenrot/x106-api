package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"

	"github.com/pkn/api/internal/config"
	"github.com/pkn/api/internal/database"
	"github.com/pkn/api/internal/worker"
)

// x106-worker: standalone consumer for the llm_jobs queue.
//
// Why a separate binary instead of a goroutine inside x106-api: the API
// process is fronted by Cloudflare's 100s ceiling, but the worker isn't —
// keeping them in separate processes lets the worker run jobs up to 180s+
// without the proxy ceiling muddying timeouts. Restarts are also independent
// so a misbehaving worker doesn't take the API down.
func main() {
	cfg := config.Load()

	if err := database.Connect(cfg.DSN()); err != nil {
		log.Fatalf("[worker] db connect: %v", err)
	}
	defer database.Close()
	if err := database.EnsureSchema(); err != nil {
		log.Fatalf("[worker] ensure schema: %v", err)
	}

	if cfg.DeepSeekAPIKey == "" {
		log.Println("[worker] WARNING: DEEPSEEK_API_KEY not set — jobs will fail until configured")
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	go func() {
		quit := make(chan os.Signal, 1)
		signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
		sig := <-quit
		log.Printf("[worker] received %s, shutting down", sig)
		cancel()
	}()

	worker.Run(ctx, cfg)
	log.Println("[worker] stopped")
}
