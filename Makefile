.PHONY: build run dev clean migrate

APP_NAME=x106-api
BUILD_DIR=bin

build:
	go build -o $(BUILD_DIR)/$(APP_NAME) ./cmd/server

run:
	go run ./cmd/server

dev:
	air

migrate:
	mysql -u root -p < migrations/001_init.sql

test:
	go test ./...

clean:
	rm -rf $(BUILD_DIR)
