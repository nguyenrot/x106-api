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

	authH    := handler.NewAuthHandler(cfg)
	userH    := handler.NewUserHandler()
	journalH := handler.NewJournalHandler()
	contentH := handler.NewContentHandler()
	adminH   := handler.NewAdminHandler(cfg)

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
