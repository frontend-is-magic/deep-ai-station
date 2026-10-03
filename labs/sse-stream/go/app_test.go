package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"reflect"
	"regexp"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
)

func TestMain(m *testing.M) { gin.SetMode(gin.TestMode); os.Exit(m.Run()) }

func routerFor(t *testing.T, config Config) http.Handler {
	t.Helper()
	router, err := NewRouter(config)
	if err != nil {
		t.Fatal(err)
	}
	return router
}
func fixtureFor(t *testing.T) Fixtures {
	t.Helper()
	fixtures, err := loadFixtures()
	if err != nil {
		t.Fatal(err)
	}
	return fixtures
}
func serverFor(t *testing.T, config Config) (*httptest.Server, *http.Client) {
	t.Helper()
	server := httptest.NewServer(routerFor(t, config))
	client := server.Client()
	client.Timeout = 3 * time.Second
	t.Cleanup(func() { client.CloseIdleConnections(); server.Close() })
	return server, client
}
func getStream(t *testing.T, client *http.Client, address string) *http.Response {
	t.Helper()
	response, err := client.Get(address)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { response.Body.Close() })
	if response.StatusCode != 200 {
		t.Fatalf("status %d", response.StatusCode)
	}
	for key, value := range map[string]string{"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", "X-Accel-Buffering": "no"} {
		if response.Header.Get(key) != value {
			t.Fatalf("header %s=%q", key, response.Header.Get(key))
		}
	}
	if response.Header.Get("Content-Type") != "text/event-stream; charset=utf-8" {
		t.Fatal("not SSE")
	}
	if response.Header.Get("Access-Control-Allow-Origin") != "" || len(response.Header.Values("Set-Cookie")) != 0 {
		t.Fatal("unexpected CORS or cookie")
	}
	return response
}

type event struct {
	Name string
	Data map[string]any
}

func readEvent(t *testing.T, reader *bufio.Reader) event {
	t.Helper()
	name, err := reader.ReadString('\n')
	if err != nil {
		t.Fatal(err)
	}
	payload, err := reader.ReadString('\n')
	if err != nil {
		t.Fatal(err)
	}
	empty, err := reader.ReadString('\n')
	if err != nil || empty != "\n" || !strings.HasPrefix(name, "event: ") || !strings.HasPrefix(payload, "data: ") {
		t.Fatalf("invalid frame %q %q %q %v", name, payload, empty, err)
	}
	result := event{Name: strings.TrimSuffix(strings.TrimPrefix(name, "event: "), "\n")}
	if err := json.Unmarshal([]byte(strings.TrimPrefix(payload, "data: ")), &result.Data); err != nil {
		t.Fatal(err)
	}
	return result
}
func requireEOF(t *testing.T, reader *bufio.Reader) {
	t.Helper()
	_, err := reader.ReadByte()
	if !errors.Is(err, io.EOF) {
		t.Fatalf("expected EOF: %v", err)
	}
}
func receive(t *testing.T, signal <-chan struct{}) {
	t.Helper()
	timer := time.NewTimer(2 * time.Second)
	defer timer.Stop()
	select {
	case <-signal:
	case <-timer.C:
		t.Fatal("signal did not arrive")
	}
}

type observedProducer struct {
	inner  Producer
	calls  atomic.Int32
	closes atomic.Int32
	closed chan struct{}
}

func observe(inner Producer) *observedProducer {
	return &observedProducer{inner: inner, closed: make(chan struct{})}
}
func (p *observedProducer) Next(ctx context.Context) (string, error) {
	p.calls.Add(1)
	return p.inner.Next(ctx)
}
func (p *observedProducer) Close() {
	if p.closes.Add(1) == 1 {
		p.inner.Close()
		close(p.closed)
	}
}

func TestHealthAndStrictQueryNeverStartsProducer(t *testing.T) {
	var starts atomic.Int32
	server, client := serverFor(t, Config{Producer: func(string) Producer { starts.Add(1); return nil }})
	response, err := client.Get(server.URL + "/health")
	if err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(response.Body)
	response.Body.Close()
	if err != nil || response.StatusCode != 200 || string(body) != `{"lab":"sse-stream","ok":true}` {
		t.Fatalf("health %s %v", body, err)
	}
	invalid := []string{"", "?scenario", "?scenario=", "?scenario=other", "?scenario=SUCCESS", "?scenario=success&scenario=error", "?scenario=success&scenario=success", "?scenario=success&extra=x", "?Scenario=success", "?scenario=success%20", "?scenario=success;extra=x", "?scenario=%FF", "?scenario=%ZZ", "?scenario=hold&=x", "?%73cenario=success&scenario=success"}
	for _, suffix := range invalid {
		t.Run(suffix, func(t *testing.T) {
			response, err := client.Get(server.URL + "/stream" + suffix)
			if err != nil {
				t.Fatal(err)
			}
			body, err := io.ReadAll(response.Body)
			response.Body.Close()
			if err != nil || response.StatusCode != 400 {
				t.Fatalf("status %d: %s %v", response.StatusCode, body, err)
			}
			if string(body) != `{"error":{"code":"invalid_request","message":"仅支持一个有效的 scenario 参数"}}` {
				t.Fatalf("unexpected error: %s", body)
			}
		})
	}
	if starts.Load() != 0 {
		t.Fatal("invalid request started producer")
	}
}

var uuidPattern = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)

func TestRealHTTPScenariosAndExactlyOnceCleanup(t *testing.T) {
	fixtures := fixtureFor(t)
	seen := map[string]bool{}
	for _, scenario := range []string{"success", "error", "timeout", "hold"} {
		t.Run(scenario, func(t *testing.T) {
			producer := observe(&fixtureProducer{scenario: scenario, texts: fixtures.Texts})
			config := Config{Producer: func(string) Producer { return producer }, Deadline: time.Minute}
			expire := make(chan context.CancelCauseFunc, 1)
			if scenario == "timeout" || scenario == "hold" {
				config.WithDeadline = func(parent context.Context, _ time.Duration) (context.Context, context.CancelFunc) {
					ctx, cancel := context.WithCancelCause(parent)
					expire <- cancel
					return deadlineContext{ctx}, func() { cancel(context.Canceled) }
				}
			}
			server, client := serverFor(t, config)
			response := getStream(t, client, server.URL+"/stream?scenario="+scenario)
			reader := bufio.NewReader(response.Body)
			start := readEvent(t, reader)
			id, _ := start.Data["run_id"].(string)
			if start.Name != "start" || len(start.Data) != 2 || start.Data["scenario"] != scenario || !uuidPattern.MatchString(id) || seen[id] {
				t.Fatalf("bad start %#v", start)
			}
			seen[id] = true
			count := 1
			if scenario == "success" {
				count = 3
			}
			for index := 0; index < count; index++ {
				delta := readEvent(t, reader)
				expected := map[string]any{"run_id": id, "seq": float64(index + 1), "text": fixtures.Texts[index]}
				if delta.Name != "delta" || !reflect.DeepEqual(delta.Data, expected) {
					t.Fatalf("delta %#v", delta)
				}
			}
			if scenario == "timeout" || scenario == "hold" {
				(<-expire)(context.DeadlineExceeded)
			}
			terminal := readEvent(t, reader)
			expected := map[string]any{"run_id": id, "seq": float64(3)}
			name := "done"
			if scenario != "success" {
				name = "error"
				code, message := "deadline_exceeded", "教学运行超时"
				if scenario == "error" {
					code, message = "producer_failed", "教学数据源失败"
				}
				expected = map[string]any{"run_id": id, "code": code, "message": message}
			}
			if terminal.Name != name || !reflect.DeepEqual(terminal.Data, expected) {
				t.Fatalf("terminal %#v", terminal)
			}
			requireEOF(t, reader)
			receive(t, producer.closed)
			if producer.closes.Load() != 1 {
				t.Fatalf("cleanup %d", producer.closes.Load())
			}
		})
	}
}

type blockedProducer struct {
	texts      []string
	index      int
	waiting    chan struct{}
	release    chan struct{}
	cancelled  chan struct{}
	waitOnce   sync.Once
	cancelOnce sync.Once
	received   context.Context
}

func blocked(texts []string) *blockedProducer {
	return &blockedProducer{texts: texts, waiting: make(chan struct{}), release: make(chan struct{}), cancelled: make(chan struct{})}
}
func (p *blockedProducer) Next(ctx context.Context) (string, error) {
	p.received = ctx
	if p.index > 0 {
		p.waitOnce.Do(func() { close(p.waiting) })
		select {
		case <-ctx.Done():
			p.cancelOnce.Do(func() { close(p.cancelled) })
			return "", ctx.Err()
		case <-p.release:
		}
	}
	if err := ctx.Err(); err != nil {
		return "", err
	}
	if p.index == len(p.texts) {
		return "", io.EOF
	}
	text := p.texts[p.index]
	p.index++
	return text, nil
}
func (p *blockedProducer) Close() {}

func TestRealHTTPDisconnectAfterStartCancelsProducer(t *testing.T) {
	producer := blocked(fixtureFor(t).Texts)
	tracked := observe(producer)
	server, client := serverFor(t, Config{Producer: func(string) Producer { return tracked }, Deadline: time.Minute})
	response := getStream(t, client, server.URL+"/stream?scenario=hold")
	if readEvent(t, bufio.NewReader(response.Body)).Name != "start" {
		t.Fatal("first frame not start")
	}
	receive(t, producer.waiting)
	// Closing an unread real HTTP response closes its connection, not only a test context.
	response.Body.Close()
	receive(t, producer.cancelled)
	receive(t, tracked.closed)
	if !errors.Is(producer.received.Err(), context.Canceled) || tracked.closes.Load() != 1 || tracked.calls.Load() != 2 {
		t.Fatal("disconnect did not cancel and clean exactly once")
	}
}

func TestRealHTTPCancelADoesNotCancelB(t *testing.T) {
	fixtures := fixtureFor(t)
	a, b := blocked(fixtures.Texts), blocked(fixtures.Texts)
	trackedA, trackedB := observe(a), observe(b)
	server, client := serverFor(t, Config{Producer: func(scenario string) Producer {
		if scenario == "hold" {
			return trackedA
		}
		return trackedB
	}, Deadline: time.Minute})
	responseA := getStream(t, client, server.URL+"/stream?scenario=hold")
	readerA := bufio.NewReader(responseA.Body)
	startA := readEvent(t, readerA)
	readEvent(t, readerA)
	receive(t, a.waiting)
	responseB := getStream(t, client, server.URL+"/stream?scenario=success")
	readerB := bufio.NewReader(responseB.Body)
	startB := readEvent(t, readerB)
	readEvent(t, readerB)
	receive(t, b.waiting)
	if startA.Data["run_id"] == startB.Data["run_id"] {
		t.Fatal("shared identity")
	}
	responseA.Body.Close()
	receive(t, a.cancelled)
	receive(t, trackedA.closed)
	if b.received.Err() != nil || trackedB.closes.Load() != 0 {
		t.Fatal("A cancellation affected B")
	}
	close(b.release)
	for index := 1; index < 3; index++ {
		frame := readEvent(t, readerB)
		if frame.Name != "delta" || frame.Data["text"] != fixtures.Texts[index] || frame.Data["seq"] != float64(index+1) {
			t.Fatalf("B delta %#v", frame)
		}
	}
	if terminal := readEvent(t, readerB); terminal.Name != "done" || terminal.Data["run_id"] != startB.Data["run_id"] {
		t.Fatalf("B terminal %#v", terminal)
	}
	requireEOF(t, readerB)
	receive(t, trackedB.closed)
	if trackedA.closes.Load() != 1 || trackedB.closes.Load() != 1 {
		t.Fatal("cleanup count")
	}
}

type deadlineContext struct{ context.Context }

func (c deadlineContext) Err() error {
	if errors.Is(context.Cause(c.Context), context.DeadlineExceeded) {
		return context.DeadlineExceeded
	}
	return c.Context.Err()
}
func TestOneTotalDeadlineAndTimeoutCleanup(t *testing.T) {
	fixtures := fixtureFor(t)
	producer := blocked(fixtures.Texts)
	tracked := observe(producer)
	var count atomic.Int32
	signal := make(chan context.CancelCauseFunc, 1)
	deadline := func(parent context.Context, d time.Duration) (context.Context, context.CancelFunc) {
		count.Add(1)
		if d != 7*time.Second {
			t.Errorf("deadline %v", d)
		}
		ctx, cancel := context.WithCancelCause(parent)
		signal <- cancel
		return deadlineContext{ctx}, func() { cancel(context.Canceled) }
	}
	server, client := serverFor(t, Config{Producer: func(string) Producer { return tracked }, Deadline: 7 * time.Second, WithDeadline: deadline})
	response := getStream(t, client, server.URL+"/stream?scenario=timeout")
	reader := bufio.NewReader(response.Body)
	start := readEvent(t, reader)
	readEvent(t, reader)
	receive(t, producer.waiting)
	cancel := <-signal
	cancel(context.DeadlineExceeded)
	terminal := readEvent(t, reader)
	if terminal.Name != "error" || terminal.Data["code"] != "deadline_exceeded" || terminal.Data["run_id"] != start.Data["run_id"] {
		t.Fatalf("terminal %#v", terminal)
	}
	requireEOF(t, reader)
	receive(t, tracked.closed)
	if count.Load() != 1 || tracked.closes.Load() != 1 {
		t.Fatal("deadline reset or duplicate cleanup")
	}
}

type producerFunc struct {
	next   func(context.Context) (string, error)
	closed atomic.Int32
}

func (p *producerFunc) Next(ctx context.Context) (string, error) { return p.next(ctx) }
func (p *producerFunc) Close()                                   { p.closed.Add(1) }
func TestConfirmedDisconnectNeverEmitsTerminalOrDelta(t *testing.T) {
	for _, ending := range []string{"text", "eof", "error"} {
		t.Run(ending, func(t *testing.T) {
			parent, cancel := context.WithCancel(context.Background())
			defer cancel()
			p := &producerFunc{next: func(context.Context) (string, error) {
				cancel()
				switch ending {
				case "eof":
					return "", io.EOF
				case "error":
					return "", errors.New("private")
				default:
					return "late", nil
				}
			}}
			router := routerFor(t, Config{Producer: func(string) Producer { return p }})
			recorder := httptest.NewRecorder()
			request := httptest.NewRequest("GET", "/stream?scenario=success", nil).WithContext(parent)
			router.ServeHTTP(recorder, request)
			if strings.Count(recorder.Body.String(), "event:") != 1 || !strings.HasPrefix(recorder.Body.String(), "event: start\n") || p.closed.Load() != 1 {
				t.Fatalf("response after cancellation: %s", recorder.Body)
			}
		})
	}
}

type brokenWriter struct {
	header http.Header
	writes int
}

func (w *brokenWriter) Header() http.Header { return w.header }
func (w *brokenWriter) WriteHeader(int)     {}
func (w *brokenWriter) Write([]byte) (int, error) {
	w.writes++
	return 0, errors.New("private write failure")
}
func (w *brokenWriter) Flush() {}
func TestInitialWriteFailureCleansWithoutProduction(t *testing.T) {
	p := observe(&fixtureProducer{texts: fixtureFor(t).Texts})
	router := routerFor(t, Config{Producer: func(string) Producer { return p }})
	writer := &brokenWriter{header: make(http.Header)}
	router.ServeHTTP(writer, httptest.NewRequest("GET", "/stream?scenario=success", nil))
	if p.closes.Load() != 1 || p.calls.Load() != 0 || writer.writes != 1 {
		t.Fatal("write failure continued production")
	}
}
func TestAlreadyCancelledRequestNeverStartsProducer(t *testing.T) {
	starts := 0
	router := routerFor(t, Config{Producer: func(string) Producer { starts++; return nil }})
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	recorder := httptest.NewRecorder()
	router.ServeHTTP(recorder, httptest.NewRequest("GET", "/stream?scenario=success", nil).WithContext(ctx))
	if starts != 0 || recorder.Body.Len() != 0 {
		t.Fatal("cancelled request started work")
	}
}

type observedContext struct {
	context.Context
	entered chan struct{}
	once    sync.Once
}

func (c *observedContext) Done() <-chan struct{} {
	c.once.Do(func() { close(c.entered) })
	return c.Context.Done()
}
func TestFixedProducerWaitsAreCancellable(t *testing.T) {
	fixtures := fixtureFor(t)
	for _, scenario := range []string{"success", "hold", "timeout"} {
		t.Run(scenario, func(t *testing.T) {
			base, cancel := context.WithCancel(context.Background())
			defer cancel()
			ctx := &observedContext{Context: base, entered: make(chan struct{})}
			producer := &fixtureProducer{scenario: scenario, texts: fixtures.Texts, step: time.Hour}
			if text, err := producer.Next(ctx); text != fixtures.Texts[0] || err != nil {
				t.Fatal("first fixture")
			}
			ended := make(chan error, 1)
			go func() { _, err := producer.Next(ctx); ended <- err }()
			receive(t, ctx.entered)
			cancel()
			select {
			case err := <-ended:
				if !errors.Is(err, context.Canceled) {
					t.Fatal(err)
				}
			case <-time.After(2 * time.Second):
				t.Fatal("producer ignored cancellation")
			}
			if producer.index != 1 {
				t.Fatal("cancelled producer advanced")
			}
			producer.Close()
		})
	}
}
func TestFixtureDefaultsAndLoopbackPort(t *testing.T) {
	fixtures := fixtureFor(t)
	if !reflect.DeepEqual(fixtures.Texts, []string{"理解 ", "流式 ", "响应 🌱"}) || fixtures.DeadlineMS != 3000 || fixtures.StepMS != 100 {
		t.Fatalf("fixtures %#v", fixtures)
	}
	for input, want := range map[string]string{"": "127.0.0.1:8024", "8030": "127.0.0.1:8030", "65535": "127.0.0.1:65535"} {
		got, err := listenAddress(input)
		if err != nil || got != want {
			t.Fatalf("port %q: %s %v", input, got, err)
		}
	}
	for _, input := range []string{"0", "-1", "65536", "abc", "0.0.0.0:8024", " 8024"} {
		if _, err := listenAddress(input); err == nil {
			t.Fatal("accepted unsafe port", input)
		}
	}
	if _, err := NewRouter(Config{Deadline: -time.Second}); err == nil {
		t.Fatal("accepted negative deadline")
	}
}
func TestIndependentRunIDs(t *testing.T) {
	ids := map[string]bool{}
	for index := 0; index < 100; index++ {
		id := newRunID()
		if !uuidPattern.MatchString(id) || ids[id] {
			t.Fatal("non-independent UUID", fmt.Sprint(id))
		}
		ids[id] = true
	}
}
