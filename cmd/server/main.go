package main

import (
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"

	"github.com/go-chi/chi/v5"
	chimw "github.com/go-chi/chi/v5/middleware"

	"github.com/pkn/api/internal/config"
	"github.com/pkn/api/internal/database"
	"github.com/pkn/api/internal/handler"
	core "github.com/pkn/api/internal/middleware"
)

func main() {
	cfg := config.Load()

	if err := database.Connect(cfg.DSN()); err != nil {
		log.Fatalf("[db] failed to connect: %v", err)
	}
	defer database.Close()
	if err := database.EnsureSchema(); err != nil {
		log.Fatalf("[db] failed to ensure schema: %v", err)
	}

	authH := handler.NewAuthHandler(cfg)
	userH := handler.NewUserHandler()
	journalH := handler.NewJournalHandler()
	contentH := handler.NewContentHandler()
	adminH := handler.NewAdminHandler(cfg)
	artworkH := handler.NewArtworkHandler()
	llmH := handler.NewLLMHandler(cfg)
	adminArtH := handler.NewAdminArtHandler(cfg)

	r := chi.NewRouter()

	r.Use(chimw.RequestID)
	r.Use(chimw.RealIP)
	r.Use(core.Logger)
	r.Use(core.CORS(cfg))
	r.Use(chimw.Recoverer)

	// ── Shared (no auth) ──────────────────────
	r.Get("/api/v1/health", handler.Health)

	r.Route("/api/v1/auth", func(r chi.Router) {
		r.Post("/register", authH.Register)
		r.Post("/login", authH.Login)
		r.Post("/logout", authH.Logout)
	})

	// ── Public content ────────────────────────
	r.Get("/api/v1/content/{app}/{section}", contentH.GetSection)

	// ── Admin auth ────────────────────────────
	r.Post("/api/v1/admin/login", adminH.Login)
	r.Post("/api/v1/admin/logout", adminH.Logout)

	// ── Admin protected ───────────────────────
	r.Group(func(r chi.Router) {
		r.Use(core.AdminAuth(cfg))
		r.Get("/api/v1/admin/content/{app}", contentH.ListByApp)
		r.Put("/api/v1/admin/content/{app}/{section}", contentH.UpsertSection)

		// Admin "art" management — DeepSeek prompt, quotas, settings
		r.Route("/api/v1/admin/art", func(r chi.Router) {
			r.Get("/users", adminArtH.ListUsers)
			r.Put("/users/{id}/quota", adminArtH.SetUserQuota)
			r.Post("/users/{id}/quota/adjust", adminArtH.AdjustUserQuota)
			r.Delete("/users/{id}/quota", adminArtH.ResetUserQuota)
			r.Get("/llm-prompt", adminArtH.GetPrompt)
			r.Put("/llm-prompt", adminArtH.SetPrompt)
			r.Get("/stats", adminArtH.Stats)
			r.Get("/settings", adminArtH.GetSettings)
			r.Put("/settings", adminArtH.SetSettings)
			r.Get("/logs", adminArtH.ListLogs)
			r.Get("/logs/{id}", adminArtH.GetLogDetail)
		})
	})

	// ── Protected routes ──────────────────────
	r.Group(func(r chi.Router) {
		r.Use(core.Auth(cfg))

		// Shared
		r.Get("/api/v1/users/me", userH.GetMe)

		// journal.pkn.io.vn
		r.Route("/api/v1/journal/vibes", func(r chi.Router) {
			r.Get("/", journalH.ListVibes)
			r.Get("/today", journalH.GetTodayVibe)
			r.Post("/", journalH.UpsertVibe)
			r.Get("/stats", journalH.Stats)
		})

		// art.pkn.io.vn
		r.Route("/api/v1/artworks", func(r chi.Router) {
			r.Get("/", artworkH.ListArtworks)
			r.Post("/", artworkH.CreateArtwork)
			r.Get("/{id}", artworkH.GetArtwork)
			r.Delete("/{id}", artworkH.DeleteArtwork)
		})

		// art.pkn.io.vn — LLM director (DeepSeek), 5 calls/day/user.
		// Sync endpoints (random/polish/remix) kept for migration window;
		// production traffic should use the async /job pipeline so requests
		// don't hit Cloudflare's 100s ceiling.
		r.Route("/api/v1/studio/llm", func(r chi.Router) {
			r.Get("/quota", llmH.Quota)
			r.Post("/random", llmH.Random)
			r.Post("/polish", llmH.Polish)
			r.Post("/remix", llmH.Remix)

			r.Post("/job", llmH.Submit)
			r.Get("/job/{id}", llmH.GetJob)
			r.Post("/job/{id}/cancel", llmH.CancelJob)
		})
	})

	addr := ":" + cfg.ServerPort
	log.Printf("[server] X106 API starting on %s", addr)

	go func() {
		if err := http.ListenAndServe(addr, r); err != nil {
			log.Fatalf("[server] %v", err)
		}
	}()

	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	<-quit
	log.Println("[server] shutting down...")
}
