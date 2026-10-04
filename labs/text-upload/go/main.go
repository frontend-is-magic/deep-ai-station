package main

import (
	"encoding/json"
	"errors"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"time"
)

func listenAddress(port string) (string, error) {
	if port == "" {
		port = "8023"
	}
	for _, character := range port {
		if character < '0' || character > '9' {
			return "", errors.New("invalid port")
		}
	}
	value, err := strconv.ParseUint(port, 10, 16)
	if err != nil || value == 0 {
		return "", errors.New("invalid port")
	}
	return "127.0.0.1:" + strconv.FormatUint(value, 10), nil
}
func cliMode(args []string) (string, error) {
	if len(args) == 0 {
		return "memory", nil
	}
	if len(args) == 1 && (args[0] == "init" || args[0] == "--help") {
		return args[0], nil
	}
	if len(args) == 3 && args[0] == "serve" && args[1] == "--storage" && args[2] == "sqlite" {
		return "sqlite", nil
	}
	return "", errors.New("invalid_input")
}
func cliError(output io.Writer, err error) int {
	code := "repository_unavailable"
	for _, known := range []error{errUnsupportedSchema, errDatabaseMissing, errDatabaseExists, ErrResultUnconfirmed} {
		if errors.Is(err, known) {
			code = known.Error()
		}
	}
	_ = json.NewEncoder(output).Encode(map[string]string{"error": code})
	return 1
}
func runCLI(args []string, output io.Writer) int {
	mode, err := cliMode(args)
	if err != nil {
		_ = json.NewEncoder(output).Encode(map[string]string{"error": "invalid_input"})
		return 1
	}
	if mode == "--help" {
		if _, err := io.WriteString(output, "Usage: text-upload [init | serve --storage sqlite | --help]\nNo arguments: memory. SQLite uses only .data/uploads.sqlite3 in the current directory.\n"); err != nil {
			return 1
		}
		return 0
	}
	if mode == "init" {
		if err := InitializeSQLite(storageRelativePath); err != nil {
			return cliError(output, err)
		}
		if json.NewEncoder(output).Encode(map[string]any{"schema_version": 1, "storage_contract": storageContract}) != nil {
			return 1
		}
		return 0
	}
	address, err := listenAddress(os.Getenv("PORT"))
	if err != nil {
		_ = json.NewEncoder(output).Encode(map[string]string{"error": "invalid_input"})
		return 1
	}
	config := Config{}
	if mode == "sqlite" {
		repository, err := NewSQLiteRepository(storageRelativePath)
		if err != nil {
			return cliError(output, err)
		}
		config.Repository = repository
	}
	router, err := NewRouter(config)
	if err != nil {
		return cliError(output, err)
	}
	listener, err := net.Listen("tcp", address)
	if err != nil {
		return cliError(output, err)
	}
	defer listener.Close()
	server := &http.Server{Addr: address, Handler: router, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 10 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16384, ErrorLog: log.New(io.Discard, "", 0)}
	if err = server.Serve(listener); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return cliError(output, err)
	}
	return 0
}
func main() { os.Exit(runCLI(os.Args[1:], os.Stdout)) }
