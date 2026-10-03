package main

import (
	"encoding/json"
	"io"
	"os"
)

func main() {
	code := run(os.Args[1:], os.Stdin, os.Stdout)
	os.Exit(code)
}

func run(args []string, input io.Reader, output io.Writer) int {
	if len(args) != 1 || args[0] == "" {
		return emit(output, nil, errInvalid)
	}
	body, err := io.ReadAll(io.LimitReader(input, 4097))
	if err != nil {
		return emit(output, nil, errInvalid)
	}
	request, err := parseRequest(body)
	if err != nil {
		return emit(output, nil, err)
	}
	repository, err := openRepository(args[0])
	if err != nil {
		return emit(output, nil, err)
	}
	defer repository.db.Close()
	result, err := repository.execute(request)
	return emit(output, result, err)
}

func emit(output io.Writer, value any, err error) int {
	code := 0
	if err != nil {
		value = map[string]string{"error": errorCode(err)}
		code = 1
	}
	if json.NewEncoder(output).Encode(value) != nil {
		return 1
	}
	return code
}
