package middleware

import (
	"net/http"

	"github.com/pkn/api/internal/config"
)

func CORS(cfg *config.Config) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			origin := r.Header.Get("Origin")

			allowed := map[string]bool{
				"http://localhost:3000":  true,
				"http://localhost:3001":  true,
				"http://localhost:3002":  true,
				"https://pkn.io.vn":     true,
				"https://me.pkn.io.vn":   true,
				"https://journal.pkn.io.vn": true,
			}

			if cfg.IsDev() || allowed[origin] {
				w.Header().Set("Access-Control-Allow-Origin", origin)
			}
			w.Header().Set("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
			w.Header().Set("Access-Control-Allow-Headers", "Content-Type,Authorization")
			w.Header().Set("Access-Control-Allow-Credentials", "true")
			w.Header().Set("Access-Control-Max-Age", "86400")

			if r.Method == http.MethodOptions {
				w.WriteHeader(http.StatusNoContent)
				return
			}

			next.ServeHTTP(w, r)
		})
	}
}
