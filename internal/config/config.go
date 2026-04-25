package config

import (
	"os"
	"time"
)

type Config struct {
	ServerPort  string
	DBHost      string
	DBPort      string
	DBName      string
	DBUser      string
	DBPassword  string
	JWTSecret   string
	JWTDuration time.Duration
	CookieDomain string
	Env         string
}

func Load() *Config {
	return &Config{
		ServerPort:   getEnv("SERVER_PORT", "4000"),
		DBHost:       getEnv("DB_HOST", "127.0.0.1"),
		DBPort:       getEnv("DB_PORT", "3306"),
		DBName:       getEnv("DB_NAME", "x106"),
		DBUser:       getEnv("DB_USER", "root"),
		DBPassword:   getEnv("DB_PASSWORD", ""),
		JWTSecret:    getEnv("JWT_SECRET", "x106-dev-secret-change-in-production"),
		JWTDuration:  30 * 24 * time.Hour,
		CookieDomain: getEnv("COOKIE_DOMAIN", ".pkn.io.vn"),
		Env:          getEnv("ENV", "development"),
	}
}

func (c *Config) DSN() string {
	return c.DBUser + ":" + c.DBPassword + "@tcp(" + c.DBHost + ":" + c.DBPort + ")/" + c.DBName + "?parseTime=true&charset=utf8mb4&collation=utf8mb4_unicode_ci"
}

func (c *Config) IsDev() bool {
	return c.Env != "production"
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
