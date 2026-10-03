package main

import (
	"encoding/json"
	"fmt"
	"go/parser"
	"go/token"
	"os"
)

func main() {
	var examples []struct{ ID, Language, Source string }
	data, err := os.ReadFile(os.Args[1])
	if err != nil {
		panic(err)
	}
	if err = json.Unmarshal(data, &examples); err != nil {
		panic(err)
	}
	count := 0
	for _, item := range examples {
		if item.Language != "go" {
			continue
		}
		if _, err := parser.ParseFile(token.NewFileSet(), item.ID+".go", item.Source, parser.AllErrors); err != nil {
			panic(err)
		}
		count++
	}
	fmt.Printf("Go syntax checked: %d; no code executed\n", count)
}
