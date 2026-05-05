package service

import (
	"database/sql"
	"errors"

	"github.com/pkn/api/internal/database"
	"github.com/pkn/api/internal/model"
	"golang.org/x/crypto/bcrypt"
)

const userTable = "users"

var (
	ErrUserExists   = errors.New("username already taken")
	ErrInvalidCreds = errors.New("invalid username or password")
	ErrUserNotFound = errors.New("user not found")
)

func Register(req model.RegisterRequest) (*model.User, error) {
	existing, err := findByUsername(req.Username)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		return nil, ErrUserExists
	}

	hash, err := bcrypt.GenerateFromPassword([]byte(req.Password), bcrypt.DefaultCost)
	if err != nil {
		return nil, err
	}

	// Production users.created_at / updated_at default to NULL (the table is
	// shared with a legacy app), so we must set them explicitly — Scan into
	// time.Time would otherwise fail later.
	_, err = database.DB.Exec(
		`INSERT INTO `+userTable+` (username, password_hash, created_at, updated_at) VALUES (?, ?, NOW(), NOW())`,
		req.Username, string(hash),
	)
	if err != nil {
		_, err = database.DB.Exec(
			`INSERT INTO `+userTable+` (id, username, password_hash, created_at, updated_at) VALUES (?, ?, ?, NOW(), NOW())`,
			newID(), req.Username, string(hash),
		)
	}
	if err != nil {
		return nil, err
	}

	return findByUsername(req.Username)
}

func Login(req model.LoginRequest) (*model.User, error) {
	user, err := findByUsername(req.Username)
	if err != nil {
		return nil, err
	}
	if user == nil {
		return nil, ErrInvalidCreds
	}

	if err := bcrypt.CompareHashAndPassword([]byte(user.PasswordHash), []byte(req.Password)); err != nil {
		return nil, ErrInvalidCreds
	}
	return user, nil
}

func GetUserByID(userID string) (*model.User, error) {
	user := &model.User{}
	err := database.DB.QueryRow(
		`SELECT id, username, display_name, avatar_url,
		        COALESCE(created_at, NOW()), COALESCE(updated_at, NOW())
		 FROM `+userTable+` WHERE id = ?`,
		userID,
	).Scan(&user.ID, &user.Username, &user.DisplayName, &user.AvatarURL, &user.CreatedAt, &user.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, ErrUserNotFound
	}
	if err != nil {
		return nil, err
	}
	return user, nil
}

func findByUsername(username string) (*model.User, error) {
	user := &model.User{}
	err := database.DB.QueryRow(
		`SELECT id, username, password_hash, display_name, avatar_url,
		        COALESCE(created_at, NOW()), COALESCE(updated_at, NOW())
		 FROM `+userTable+` WHERE username = ?`,
		username,
	).Scan(&user.ID, &user.Username, &user.PasswordHash, &user.DisplayName, &user.AvatarURL, &user.CreatedAt, &user.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return user, nil
}
