package main

import (
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"
)

func listenAddress(port string) (string, error) {
	if port == "" {
		port = "8024"
	}
	value, err := strconv.ParseUint(port, 10, 16)
	if err != nil || value == 0 {
		return "", errors.New("PORT must be an integer between 1 and 65535")
	}
	return "127.0.0.1:" + strconv.FormatUint(value, 10), nil
}

func main() {
	gin.SetMode(gin.ReleaseMode)
	address, err := listenAddress(os.Getenv("PORT"))
	if err != nil {
		log.Fatal("PORT must be an integer between 1 and 65535")
	}
	router, err := NewRouter(Config{})
	if err != nil {
		log.Fatal("fixed teaching fixtures unavailable")
	}
	server := &http.Server{Addr: address, Handler: router, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16384, ErrorLog: log.New(io.Discard, "", 0)}
	if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		log.Fatal("SSE teaching server stopped")
	}
}
