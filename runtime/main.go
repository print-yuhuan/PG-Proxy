// Local JavaScript VM helper; no HTTP listener or API business logic.
// Browser host adapted from AIStudio2API, MIT; see ../NOTICE.
package main

import (
	"bufio"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"local/gemini-playground-waa/waa"
	"os"
	stdruntime "runtime"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type Input struct {
	ID          int               `json:"id"`
	Op          string            `json:"op"`
	Challenge   map[string]string `json:"challenge"`
	Interpreter string            `json:"interpreter"`
	PageURL     string            `json:"page_url"`
	Digest      string            `json:"digest"`
	Prompt      string            `json:"prompt"`
	Globals     map[string]any    `json:"globals"`
	Cookies     []map[string]any  `json:"cookies"`
	Storage     map[string]string `json:"local_storage"`
	TimeZone    string            `json:"timezone"`
	Timeout     float64           `json:"timeout_seconds"`
}
type ResourceResult struct {
	ResourceID int64          `json:"resource_id"`
	OK         bool           `json:"ok"`
	Body       string         `json:"body"`
	Headers    map[string]any `json:"headers"`
}

func main() {
	scanner := bufio.NewScanner(os.Stdin)
	scanner.Buffer(make([]byte, 65536), 8<<20)
	var runtime *waa.Runtime
	defer func() {
		if runtime != nil {
			runtime.Close()
		}
	}()
	root, cancelRoot := context.WithCancel(context.Background())
	defer cancelRoot()
	operations := make(chan Input, 1)
	var outputMu, pendingMu sync.Mutex
	pending := map[int64]chan ResourceResult{}
	var sequence atomic.Int64
	emit := func(value any) { outputMu.Lock(); defer outputMu.Unlock(); json.NewEncoder(os.Stdout).Encode(value) }
	go func() {
		defer close(operations)
		defer cancelRoot()
		for scanner.Scan() {
			var in Input
			if json.Unmarshal(scanner.Bytes(), &in) != nil {
				continue
			}
			if in.Op == "resource_result" {
				var result ResourceResult
				json.Unmarshal(scanner.Bytes(), &result)
				pendingMu.Lock()
				channel := pending[result.ResourceID]
				pendingMu.Unlock()
				if channel != nil {
					channel <- result
				}
			} else {
				operations <- in
			}
		}
	}()
	loadImage := func(address string) bool {
		id := sequence.Add(1)
		channel := make(chan ResourceResult, 1)
		pendingMu.Lock()
		pending[id] = channel
		pendingMu.Unlock()
		defer func() { pendingMu.Lock(); delete(pending, id); pendingMu.Unlock() }()
		emit(map[string]any{"resource": map[string]any{"id": id, "url": address, "referrer": "https://aistudio.google.com/"}})
		select {
		case result := <-channel:
			body, err := base64.StdEncoding.DecodeString(result.Body)
			return result.OK && err == nil && len(body) > 0 && strings.HasPrefix(fmt.Sprint(result.Headers["content-type"]), "image/")
		case <-time.After(30 * time.Second):
			return false
		case <-root.Done():
			return false
		}
	}
	for in := range operations {
		timeout := 45 * time.Second
		if in.Timeout > 0 && in.Timeout <= 600 {
			timeout = time.Duration(in.Timeout * float64(time.Second))
		}
		ctx, cancel := context.WithTimeout(root, timeout)
		var result any
		var err error
		switch in.Op {
		case "init":
			if runtime != nil {
				runtime.Close()
				runtime = nil
			}
			c := in.Challenge
			if waa.InterpreterHash([]byte(in.Interpreter)) != c["interpreter_hash"] {
				err = fmt.Errorf("interpreter hash mismatch")
				break
			}
			zone := in.TimeZone
			if zone == "" {
				zone = "Asia/Shanghai"
			}
			cookie := ""
			for _, entry := range in.Cookies {
				if entry["httpOnly"] != true {
					cookie += fmt.Sprint(entry["name"]) + "=" + fmt.Sprint(entry["value"]) + "; "
				}
			}
			profile := waa.Profile{PageURL: in.PageURL, Locale: "en-US", TimeZone: zone, HasFocus: true, Globals: in.Globals, LocalStorage: in.Storage, Document: map[string]any{"cookie": cookie}, Navigator: map[string]any{"userAgent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0", "platform": "Win32", "language": "en-US", "languages": []any{"en-US", "en"}, "hardwareConcurrency": 8}, Window: map[string]any{"outerWidth": 1280, "outerHeight": 900, "innerWidth": 1280, "innerHeight": 843, "devicePixelRatio": 1, "screenX": 0, "screenY": 0}, Screen: map[string]any{"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1040, "colorDepth": 24, "pixelDepth": 24}}
			runtime, err = waa.NewRuntime(ctx, waa.Options{Challenge: waa.Challenge{InterpreterHash: c["interpreter_hash"], InterpreterURL: c["interpreter_url"], Program: c["program"], GlobalName: c["global_name"], ClientExperimentsStateBlob: c["experiments"]}, Interpreter: in.Interpreter, Profile: profile, LoadImage: loadImage})
			if err == nil {
				err = runtime.FillPrompt(ctx, "Hello", true)
			}
			if err == nil {
				err = runtime.Settle(ctx)
			}
			result = map[string]any{"initialized": err == nil}
		case "proof", "refresh":
			var value string
			if runtime == nil {
				err = fmt.Errorf("not initialized")
			} else if in.Op == "proof" {
				value, err = runtime.Proof(ctx, in.Digest, in.Prompt)
			} else {
				value, err = runtime.RefreshSnapshot(ctx)
			}
			result = map[string]any{"proof": value}
		case "ping":
			result = map[string]any{"runtime": "goja-firefox", "goos": stdruntime.GOOS, "goarch": stdruntime.GOARCH}
		default:
			err = fmt.Errorf("unsupported operation")
		}
		cancel()
		if err != nil {
			emit(map[string]any{"id": in.ID, "error": "WAA VM operation failed"})
		} else {
			emit(map[string]any{"id": in.ID, "result": result})
		}
	}
}
