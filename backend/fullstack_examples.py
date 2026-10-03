"""Equivalent engineering concepts across Python, TypeScript and Go."""

# ruff: noqa: E101
# gofmt tabs belong to embedded Go strings; executable Python uses spaces.

from backend.examples import code


def example(python: str, typescript: str, go: str) -> dict[str, str]:
    return {"python": code(python), "typescript": code(typescript), "go": code(go)}


FULLSTACK_EXAMPLES = {
    "http": example(
        r"""
        from fastapi import FastAPI, HTTPException

        app = FastAPI()
        DOCUMENTS = {"mcp": {"id": "mcp", "title": "工具协议"}}


        @app.get("/documents/{document_id}")
        def get_document(document_id: str):
            if document_id not in DOCUMENTS:
                raise HTTPException(404, "document_not_found")
            return DOCUMENTS[document_id]
    """,
        r"""
        import { Hono } from 'hono';
        const app = new Hono();
        const documents = new Map([['mcp', { id: 'mcp', title: '工具协议' }]]);
        app.get('/documents/:id', (c) => {
          const document = documents.get(c.req.param('id'));
          return document ? c.json(document) : c.json({ error: 'document_not_found' }, 404);
        });
        export default app;
    """,
        r"""
        package main

        import (
        	"encoding/json"
        	"net/http"
        )

        func main() {
        	mux := http.NewServeMux()
        	mux.HandleFunc("GET /documents/{id}", func(w http.ResponseWriter, r *http.Request) {
        		w.Header().Set("Content-Type", "application/json")
        		if r.PathValue("id") != "mcp" {
        			w.WriteHeader(404)
        			json.NewEncoder(w).Encode(map[string]string{"error": "document_not_found"})
        			return
        		}
        		json.NewEncoder(w).Encode(map[string]string{"id": "mcp", "title": "工具协议"})
        	})
        	http.ListenAndServe("127.0.0.1:8080", mux)
        }
    """,
    ),
    "types": example(
        r"""
        from pydantic import BaseModel, ConfigDict, Field, ValidationError


        class Query(BaseModel):
            model_config = ConfigDict(extra="forbid")
            question: str = Field(min_length=1, max_length=500)
            limit: int = Field(ge=1, le=10, default=3)


        def parse_query(raw):
            try:
                return Query.model_validate(raw).model_dump()
            except ValidationError:
                return {"code": "invalid_input", "message": "检查 question 与 limit"}


        print(parse_query({"question": "工具协议", "limit": 3}))
    """,
        r"""
        type Query = { question: string; limit: number };
        export function parseQuery(raw: unknown): Query {
          if (!raw || typeof raw !== 'object') throw new Error('invalid_input');
          const x = raw as Record<string, unknown>;
          if (
            Object.keys(x).some((k) => !['question', 'limit'].includes(k)) ||
            typeof x.question !== 'string' ||
            !x.question.trim() ||
            x.question.length > 500 ||
            !Number.isInteger(x.limit) ||
            Number(x.limit) < 1 ||
            Number(x.limit) > 10
          ) {
            throw new Error('invalid_input');
          }
          return { question: x.question, limit: x.limit as number };
        }
    """,
        r"""
        package main

        import (
        	"encoding/json"
        	"errors"
        	"strings"
        )

        type Query struct {
        	Question string `json:"question"`
        	Limit    int    `json:"limit"`
        }

        func parseQuery(raw string) (Query, error) {
        	var q Query
        	decoder := json.NewDecoder(strings.NewReader(raw))
        	decoder.DisallowUnknownFields()
        	if err := decoder.Decode(&q); err != nil {
        		return q, err
        	}
        	if strings.TrimSpace(q.Question) == "" || len([]rune(q.Question)) > 500 || q.Limit < 1 || q.Limit > 10 {
        		return q, errors.New("invalid_input")
        	}
        	return q, nil
        }
        func main() {}
    """,
    ),
    "toolchain": example(
        r"""
        from pathlib import Path


        def project_checks(root: Path):
            required = ["pyproject.toml", "uv.lock", "AGENTS.md"]
            return {name: (root / name).is_file() for name in required}


        # uv sync --locked; uv run ruff check .; uv run pytest
        print(project_checks(Path(".")))
    """,
        r"""
        import { existsSync } from 'node:fs';
        export function projectChecks() {
          return Object.fromEntries(
            ['package.json', 'pnpm-lock.yaml', 'tsconfig.json'].map((name) => [name, existsSync(name)]),
          );
        }
        // pnpm install --frozen-lockfile; pnpm exec tsc --noEmit; pnpm test
    """,
        r"""
        package main

        import (
        	"fmt"
        	"os"
        )

        func projectChecks() map[string]bool {
        	checks := map[string]bool{}
        	for _, name := range []string{"go.mod", "go.sum", "AGENTS.md"} {
        		_, err := os.Stat(name)
        		checks[name] = err == nil
        	}
        	return checks
        }

        // go mod download; gofmt -w .; go test ./...
        func main() { fmt.Println(projectChecks()) }
    """,
    ),
    "components": example(
        r"""
        from fastapi import FastAPI
        from pydantic import BaseModel


        class LessonCard(BaseModel):
            id: str
            title: str
            completed: bool


        app = FastAPI()


        @app.get("/cards", response_model=list[LessonCard])
        def cards():
            return [LessonCard(id="mcp", title="工具协议", completed=False)]


        # React 组件读取这个契约；Python 服务不承担浏览器渲染。
    """,
        r"""
        import { createElement } from 'react';
        type Props = { title: string; completed: boolean; onOpen: () => void };
        export function LessonCard({ title, completed, onOpen }: Props) {
          return createElement(
            'button',
            { onClick: onOpen, type: 'button' },
            createElement('strong', null, title),
            createElement('span', null, completed ? '已完成' : '开始学习'),
          );
        }
        // 展示组件接收 props；数据请求、加载和错误状态由父组件管理。
    """,
        r"""
        package main

        import (
        	"encoding/json"
        	"net/http"
        )

        type LessonCard struct {
        	ID        string `json:"id"`
        	Title     string `json:"title"`
        	Completed bool   `json:"completed"`
        }

        func cards(w http.ResponseWriter, r *http.Request) {
        	w.Header().Set("Content-Type", "application/json")
        	json.NewEncoder(w).Encode([]LessonCard{{ID: "mcp", Title: "工具协议", Completed: false}})
        }
        func main() { http.HandleFunc("GET /cards", cards); http.ListenAndServe("127.0.0.1:8080", nil) }
    """,
    ),
    "jotai": example(
        r"""
        from typing import Literal
        from pydantic import BaseModel


        class Preferences(BaseModel):
            version: Literal[1] = 1
            language: Literal["typescript", "go", "python"] = "python"


        def validate_preferences(raw):
            return Preferences.model_validate(raw).model_dump()


        # localStorage 由浏览器负责；云同步需要单独的用户与授权契约。
        print(validate_preferences({"version": 1, "language": "go"}))
    """,
        r"""
        import { atom } from 'jotai';
        import { atomWithStorage } from 'jotai/utils';
        export const completedAtom = atomWithStorage<string[]>('completed:v1', []);
        export const completedCountAtom = atom((get) => get(completedAtom).length);
        export const completeLessonAtom = atom(null, (get, set, id: string) => {
          const ids = get(completedAtom);
          if (!ids.includes(id)) set(completedAtom, [...ids, id]);
        });
        // 派生值随 atom 更新；持久化只覆盖当前浏览器。
    """,
        r"""
        package main

        import "errors"

        type Preferences struct {
        	Version  int
        	Language string
        }

        func validatePreferences(p Preferences) error {
        	if p.Version != 1 {
        		return errors.New("unsupported_version")
        	}
        	switch p.Language {
        	case "typescript", "go", "python":
        		return nil
        	}
        	return errors.New("invalid_language")
        }

        // Go 服务验证同步契约，不直接操作浏览器 localStorage。
        func main() {}
    """,
    ),
    "design": example(
        r"""
        def button_tokens(variant, disabled=False):
            variants = {
                "primary": {"background": "#355b39", "foreground": "#ffffff"},
                "outline": {"background": "#ffffff", "foreground": "#355b39"},
            }
            if variant not in variants:
                raise ValueError("unknown_variant")
            return {**variants[variant], "disabled": disabled, "minimum_height": 44}


        print(button_tokens("primary"))
        # 颜色与尺寸通过共享设计 token 向前端传递。
    """,
        r"""
        import { cva } from 'class-variance-authority';
        const button = cva(
          'inline-flex min-h-11 items-center rounded-lg px-4 focus-visible:ring-2 disabled:opacity-50',
          {
            variants: {
              variant: {
                primary: 'bg-green-900 text-white',
                outline: 'border border-green-900 text-green-900',
              },
            },
            defaultVariants: { variant: 'primary' },
          },
        );
        export function buttonClass(variant: 'primary' | 'outline') {
          return button({ variant });
        }
        // 将交互状态与样式变体集中到源码组件，避免每页重复拼接。
    """,
        r"""
        package main

        import (
        	"errors"
        	"fmt"
        )

        type Tokens struct {
        	Background, Foreground string
        	MinimumHeight          int
        }

        func buttonTokens(variant string) (Tokens, error) {
        	switch variant {
        	case "primary":
        		return Tokens{"#355b39", "#ffffff", 44}, nil
        	case "outline":
        		return Tokens{"#ffffff", "#355b39", 44}, nil
        	default:
        		return Tokens{}, errors.New("unknown_variant")
        	}
        }
        func main() { tokens, _ := buttonTokens("primary"); fmt.Println(tokens) }
    """,
    ),
    "routing": example(
        r"""
        from fastapi import FastAPI, HTTPException

        app = FastAPI()


        def lesson_service(lesson_id):
            return {"id": lesson_id, "title": "工具契约"} if lesson_id == "tools" else None


        @app.get("/lessons/{lesson_id}")
        def get_lesson(lesson_id: str):
            result = lesson_service(lesson_id)
            if result is None:
                raise HTTPException(404, "lesson_not_found")
            return result


        # 路由处理 HTTP；service 可在没有请求对象时单独测试。
    """,
        r"""
        import { Hono } from 'hono';
        const app = new Hono();
        function lessonService(id: string) {
          return id === 'tools' ? { id, title: '工具契约' } : null;
        }
        app.get('/lessons/:id', (c) => {
          const result = lessonService(c.req.param('id'));
          return result ? c.json(result) : c.json({ error: 'lesson_not_found' }, 404);
        });
        export { lessonService };
        export default app;
    """,
        r"""
        package main

        import "github.com/gin-gonic/gin"

        func lessonService(id string) (gin.H, bool) {
        	if id != "tools" {
        		return nil, false
        	}
        	return gin.H{"id": id, "title": "工具契约"}, true
        }
        func main() {
        	app := gin.Default()
        	app.GET("/lessons/:id", func(c *gin.Context) {
        		result, ok := lessonService(c.Param("id"))
        		if !ok {
        			c.JSON(404, gin.H{"error": "lesson_not_found"})
        			return
        		}
        		c.JSON(200, result)
        	})
        	app.Run("127.0.0.1:8080")
        }
    """,
    ),
    "async": example(
        r"""
        import asyncio


        async def retrieve(query):
            await asyncio.sleep(0.01)
            return {"query": query, "count": 2}


        async def bounded_search(query):
            try:
                async with asyncio.timeout(1):
                    return await retrieve(query)
            except TimeoutError:
                return {"error": "search_timeout"}


        print(asyncio.run(bounded_search("工具契约")))
    """,
        r"""
        export async function boundedSearch(url: string, signal?: AbortSignal) {
          const timeout = AbortSignal.timeout(1000);
          const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
          const response = await fetch(url, { signal: combined });
          if (!response.ok) throw new Error('search_failed');
          return response.json();
        }
        // 调用方通过 AbortController.abort() 将取消传给 fetch。
    """,
        r"""
        package main

        import (
        	"context"
        	"net/http"
        	"time"
        )

        func boundedSearch(parent context.Context, url string) error {
        	ctx, cancel := context.WithTimeout(parent, time.Second)
        	defer cancel()
        	req, err := http.NewRequestWithContext(ctx, "GET", url, nil)
        	if err != nil {
        		return err
        	}
        	response, err := http.DefaultClient.Do(req)
        	if err != nil {
        		return err
        	}
        	defer response.Body.Close()
        	return nil
        }
        func main() {}
    """,
    ),
    "validation": example(
        r"""
        from typing import Annotated
        from fastapi import Depends, FastAPI
        from pydantic import BaseModel, Field

        app = FastAPI()


        class Input(BaseModel):
            question: str = Field(min_length=1, max_length=500)


        def repository():
            return {"tools": "先校验输入与权限"}


        @app.post("/search")
        def search(body: Input, docs: Annotated[dict, Depends(repository)]):
            return {"question": body.question, "items": list(docs.values())}


        # 测试时用 dependency_overrides 替换 repository。
    """,
        r"""
        import { Hono } from 'hono';
        type Repository = { search: (question: string) => Promise<string[]> };
        export function createApp(repository: Repository) {
          const app = new Hono();
          app.post('/search', async (c) => {
            const body = await c.req.json().catch(() => null);
            if (
              !body ||
              typeof body.question !== 'string' ||
              !body.question.trim() ||
              body.question.length > 500
            )
              return c.json({ error: 'invalid_input' }, 422);
            return c.json({ items: await repository.search(body.question) });
          });
          return app;
        }
    """,
        r"""
        package main

        import (
        	"encoding/json"
        	"net/http"
        	"strings"
        )

        type Repository interface{ Search(string) []string }

        func searchHandler(repo Repository) http.HandlerFunc {
        	return func(w http.ResponseWriter, r *http.Request) {
        		var body struct {
        			Question string `json:"question"`
        		}
        		decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
        		decoder.DisallowUnknownFields()
        		if decoder.Decode(&body) != nil || strings.TrimSpace(body.Question) == "" || len([]rune(body.Question)) > 500 {
        			http.Error(w, "invalid_input", 422)
        			return
        		}
        		json.NewEncoder(w).Encode(repo.Search(body.Question))
        	}
        }
        func main() {}
    """,
    ),
    "database": example(
        r"""
        import sqlite3


        def find_documents(db, user_id):
            # 参数化查询与 owner 过滤一起构成数据边界。
            return db.execute("SELECT id, title FROM documents WHERE owner_id = ?", (user_id,)).fetchall()


        db = sqlite3.connect(":memory:")
        db.execute(
            "CREATE TABLE documents (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL)"
        )
        db.execute("INSERT INTO documents VALUES (?, ?, ?)", ("doc-1", "alice", "工具协议"))
        print(find_documents(db, "alice"))
        print(find_documents(db, "bob"))
    """,
        r"""
        type Database = { query: <T>(sql: string, parameters: unknown[]) => Promise<T[]> };
        type Document = { id: string; title: string };
        export function findDocuments(db: Database, userId: string) {
          return db.query<Document>('SELECT id, title FROM documents WHERE owner_id = $1', [userId]);
        }
        // 由 PostgreSQL 驱动实现 Database；禁止把 userId 拼接到 SQL。
    """,
        r"""
        package main

        import (
        	"context"
        	"database/sql"
        )

        type Document struct{ ID, Title string }

        func findDocuments(ctx context.Context, db *sql.DB, owner string) ([]Document, error) {
        	rows, err := db.QueryContext(ctx, "SELECT id, title FROM documents WHERE owner_id = $1", owner)
        	if err != nil {
        		return nil, err
        	}
        	defer rows.Close()
        	docs := []Document{}
        	for rows.Next() {
        		var d Document
        		if err := rows.Scan(&d.ID, &d.Title); err != nil {
        			return nil, err
        		}
        		docs = append(docs, d)
        	}
        	return docs, rows.Err()
        }

        // 需要注册 PostgreSQL 驱动并由调用方管理 db 生命周期。
        func main() {}
    """,
    ),
    "migrations": example(
        r"""
        import sqlite3


        def create_document(db, document_id, owner):
            with db:
                db.execute("INSERT INTO documents VALUES (?, ?)", (document_id, owner))
                db.execute("INSERT INTO audit VALUES (?, ?)", (document_id, "created"))


        db = sqlite3.connect(":memory:")
        db.executescript(
            "CREATE TABLE documents(id TEXT PRIMARY KEY, owner TEXT); CREATE TABLE audit(id TEXT, action TEXT);"
        )
        create_document(db, "doc-1", "alice")
        print(db.execute("SELECT * FROM audit").fetchall())
        # 文档与审计必须一起成功；迁移版本另行记录并在测试环境演练。
    """,
        r"""
        type Transaction = { execute: (sql: string, values: unknown[]) => Promise<void> };
        type Database = { transaction: <T>(fn: (tx: Transaction) => Promise<T>) => Promise<T> };
        export async function createDocument(db: Database, id: string, owner: string) {
          await db.transaction(async (tx) => {
            await tx.execute('INSERT INTO documents(id, owner) VALUES ($1, $2)', [id, owner]);
            await tx.execute('INSERT INTO audit(id, action) VALUES ($1, $2)', [id, 'created']);
          });
        }
        // transaction 适配器必须在异常时 rollback；迁移由独立版本脚本执行。
    """,
        r"""
        package main

        import (
        	"context"
        	"database/sql"
        )

        func createDocument(ctx context.Context, db *sql.DB, id, owner string) error {
        	tx, err := db.BeginTx(ctx, nil)
        	if err != nil {
        		return err
        	}
        	defer tx.Rollback()
        	if _, err = tx.ExecContext(ctx, "INSERT INTO documents(id, owner) VALUES ($1, $2)", id, owner); err != nil {
        		return err
        	}
        	if _, err = tx.ExecContext(ctx, "INSERT INTO audit(id, action) VALUES ($1, $2)", id, "created"); err != nil {
        		return err
        	}
        	return tx.Commit()
        }
        func main() {}
    """,
    ),
    "auth": example(
        r"""
        from fastapi import HTTPException


        def authorize_document(authenticated_user, document):
            # 用户身份应由可信会话中间件提供，不能从请求 owner_id 推断。
            if not authenticated_user:
                raise HTTPException(401, "authentication_required")
            if document["owner_id"] != authenticated_user:
                raise HTTPException(403, "forbidden")
            return document


        print(authorize_document("alice", {"id": "doc-1", "owner_id": "alice"}))
    """,
        r"""
        type Document = { id: string; ownerId: string };
        export function authorizeDocument(authenticatedUser: string | null, document: Document) {
          if (!authenticatedUser) throw new Error('authentication_required');
          if (document.ownerId !== authenticatedUser) throw new Error('forbidden');
          return document;
        }
        // authenticatedUser 来自校验后的服务端会话；不能相信浏览器传来的 ownerId。
    """,
        r"""
        package main

        import "errors"

        type Document struct{ ID, OwnerID string }

        func authorizeDocument(authenticatedUser string, document Document) error {
        	if authenticatedUser == "" {
        		return errors.New("authentication_required")
        	}
        	if document.OwnerID != authenticatedUser {
        		return errors.New("forbidden")
        	}
        	return nil
        }
        func main() {}
    """,
    ),
    "providers": example(
        r"""
        from typing import Protocol


        class Provider(Protocol):
            async def generate(self, prompt: str, max_tokens: int) -> dict: ...


        async def answer(provider: Provider, prompt: str):
            if not prompt.strip() or len(prompt) > 4000:
                raise ValueError("invalid_prompt")
            result = await provider.generate(prompt, max_tokens=1200)
            return {"answer": result["answer"], "usage": result.get("usage")}


        # OpenAI/Anthropic/DeepSeek 适配器实现同一契约；usage 不自行编造。
    """,
        r"""
        interface Provider {
          generate(input: {
            prompt: string;
            maxTokens: number;
            signal?: AbortSignal;
          }): Promise<{ answer: string; usage?: unknown }>;
        }
        export async function answer(provider: Provider, prompt: string, signal?: AbortSignal) {
          if (!prompt.trim() || prompt.length > 4000) throw new Error('invalid_prompt');
          const result = await provider.generate({ prompt, maxTokens: 1200, signal });
          return { answer: result.answer, usage: result.usage ?? null };
        }
        // Provider 内部从服务端配置读取密钥，浏览器只选择供应商 ID。
    """,
        r"""
        package main

        import (
        	"context"
        	"errors"
        	"strings"
        )

        type Result struct {
        	Answer string
        	Usage  any
        }
        type Provider interface {
        	Generate(context.Context, string, int) (Result, error)
        }

        func answer(ctx context.Context, provider Provider, prompt string) (Result, error) {
        	if strings.TrimSpace(prompt) == "" || len([]rune(prompt)) > 4000 {
        		return Result{}, errors.New("invalid_prompt")
        	}
        	return provider.Generate(ctx, prompt, 1200)
        }
        func main() {}
    """,
    ),
    "ai-stream": example(
        r"""
        import asyncio, json
        from fastapi import FastAPI, Request
        from fastapi.responses import StreamingResponse

        app = FastAPI()


        @app.get("/events")
        async def events(request: Request):
            async def frames():
                # 教学帧；实际接入时迭代供应商增量输出。
                for text in ["检索资料", "整理证据", "完成回答"]:
                    if await request.is_disconnected():
                        return
                    yield "event: delta\ndata: " + json.dumps({"text": text}) + "\n\n"
                    await asyncio.sleep(0.1)
                yield 'event: done\ndata: {"usage":null}\n\n'

            return StreamingResponse(
                frames(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
            )
    """,
        r"""
        import { Hono } from 'hono';
        import { streamSSE } from 'hono/streaming';
        const app = new Hono();
        app.get('/events', (c) =>
          streamSSE(c, async (stream) => {
            // 教学帧；stream.aborted 用于退出已断开的响应。
            for (const text of ['检索资料', '整理证据', '完成回答']) {
              if (stream.aborted) return;
              await stream.writeSSE({ event: 'delta', data: JSON.stringify({ text }) });
              await stream.sleep(100);
            }
            await stream.writeSSE({ event: 'done', data: JSON.stringify({ usage: null }) });
          }),
        );
        export default app;
    """,
        r"""
        package main

        import (
        	"encoding/json"
        	"fmt"
        	"net/http"
        	"time"
        )

        func events(w http.ResponseWriter, r *http.Request) {
        	flusher, ok := w.(http.Flusher)
        	if !ok {
        		http.Error(w, "stream_unsupported", 500)
        		return
        	}
        	w.Header().Set("Content-Type", "text/event-stream")
        	w.Header().Set("Cache-Control", "no-store")
        	for _, text := range []string{"检索资料", "整理证据", "完成回答"} {
        		select {
        		case <-r.Context().Done():
        			return
        		case <-time.After(100 * time.Millisecond):
        		}
        		data, _ := json.Marshal(map[string]string{"text": text})
        		if _, err := fmt.Fprintf(w, "event: delta\ndata: %s\n\n", data); err != nil {
        			return
        		}
        		flusher.Flush()
        	}
        	fmt.Fprint(w, "event: done\ndata: {}\n\n")
        	flusher.Flush()
        }
        func main() { http.HandleFunc("GET /events", events); http.ListenAndServe("127.0.0.1:8080", nil) }
    """,
    ),
    "ai-rag": example(
        r"""
        def grounded_input(question, documents, user_id):
            authorized = [d for d in documents if d["owner_id"] == user_id]
            if not authorized:
                return {"error": "no_authorized_evidence"}
            return {
                "question": question,
                "context": [d["text"] for d in authorized],
                "citations": [{"id": d["id"], "url": d["url"]} for d in authorized],
            }


        print(
            grounded_input(
                "解释工具协议",
                [
                    {
                        "id": "mcp",
                        "owner_id": "alice",
                        "text": "只读查询",
                        "url": "https://modelcontextprotocol.io",
                    }
                ],
                "alice",
            )
        )
    """,
        r"""
        type Document = { id: string; ownerId: string; text: string; url: string };
        export function groundedInput(question: string, documents: Document[], userId: string) {
          const authorized = documents.filter((d) => d.ownerId === userId);
          if (!authorized.length) return { error: 'no_authorized_evidence' };
          return {
            question,
            context: authorized.map((d) => d.text),
            citations: authorized.map(({ id, url }) => ({ id, url })),
          };
        }
        // 检索阶段执行访问过滤；不能先生成答案再删除越权引用。
    """,
        r"""
        package main

        import "errors"

        type Document struct{ ID, OwnerID, Text, URL string }

        func authorizedEvidence(documents []Document, user string) ([]Document, error) {
        	result := []Document{}
        	for _, d := range documents {
        		if d.OwnerID == user {
        			result = append(result, d)
        		}
        	}
        	if len(result) == 0 {
        		return nil, errors.New("no_authorized_evidence")
        	}
        	return result, nil
        }
        func main() {}
    """,
    ),
    "unit-tests": example(
        r"""
        import pytest


        def calculate_budget(tokens, price_per_million):
            if tokens < 0 or price_per_million < 0:
                raise ValueError("invalid_budget")
            return tokens * price_per_million / 1_000_000


        def test_budget():
            assert calculate_budget(1000, 2) == pytest.approx(0.002)
            with pytest.raises(ValueError):
                calculate_budget(-1, 2)


        # uv run pytest example.py
    """,
        r"""
        import { expect, test } from 'vitest';
        export function calculateBudget(tokens: number, pricePerMillion: number) {
          if (tokens < 0 || pricePerMillion < 0) throw new Error('invalid_budget');
          return (tokens * pricePerMillion) / 1_000_000;
        }
        test('budget handles success and invalid inputs', () => {
          expect(calculateBudget(1000, 2)).toBeCloseTo(0.002);
          expect(() => calculateBudget(-1, 2)).toThrow('invalid_budget');
        });
    """,
        r"""
        package budget

        import (
        	"errors"
        	"testing"
        )

        func Calculate(tokens int, price float64) (float64, error) {
        	if tokens < 0 || price < 0 {
        		return 0, errors.New("invalid_budget")
        	}
        	return float64(tokens) * price / 1000000, nil
        }
        func TestCalculate(t *testing.T) {
        	got, err := Calculate(1000, 2)
        	if err != nil || got != 0.002 {
        		t.Fatalf("unexpected result: %v %v", got, err)
        	}
        	if _, err := Calculate(-1, 2); err == nil {
        		t.Fatal("negative tokens must fail")
        	}
        }

        // 保存为 budget_test.go；go test ./...
    """,
    ),
    "browser-tests": example(
        r"""
        from playwright.sync_api import expect, sync_playwright


        def test_learning_flow():
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto("http://127.0.0.1:5173/feed")
                page.get_by_role(
                    "button", name="收藏：MCP：把工具接入变成清晰的协议边界", exact=True
                ).click()
                page.reload()
                expect(
                    page.get_by_role(
                        "button", name="取消收藏：MCP：把工具接入变成清晰的协议边界", exact=True
                    )
                ).to_be_visible()
                browser.close()


        # 验证刷新后的行为，而不是仅检查元素存在。
    """,
        r"""
        import { test, expect } from '@playwright/test';
        export const baseURL = 'http://127.0.0.1:5173';
        test('bookmark survives reload', async ({ page }) => {
          await page.goto(baseURL + '/feed');
          await page
            .getByRole('button', { name: '收藏：MCP：把工具接入变成清晰的协议边界', exact: true })
            .click();
          await page.reload();
          await expect(
            page.getByRole('button', { name: '取消收藏：MCP：把工具接入变成清晰的协议边界', exact: true }),
          ).toBeVisible();
        });
    """,
        r"""
        package contract

        import (
        	"encoding/json"
        	"net/http"
        	"net/http/httptest"
        	"testing"
        )

        func TestFeedContract(t *testing.T) {
        	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        		json.NewEncoder(w).Encode(map[string]any{"items": []any{}})
        	})
        	response := httptest.NewRecorder()
        	handler.ServeHTTP(response, httptest.NewRequest("GET", "/feed", nil))
        	var data struct {
        		Items []any `json:"items"`
        	}
        	if response.Code != 200 || json.Unmarshal(response.Body.Bytes(), &data) != nil || data.Items == nil {
        		t.Fatal("invalid feed contract")
        	}
        }

        // Go 验证服务契约；完整浏览器流程使用旁边的 Python/TS headless 示例。
    """,
    ),
    "app-security": example(
        r"""
        from urllib.parse import urlparse

        ALLOWED_HOSTS = {"go.dev", "docs.python.org"}


        def approved_source(url):
            parsed = urlparse(url)
            return (
                parsed.scheme == "https"
                and parsed.hostname in ALLOWED_HOSTS
                and not parsed.username
                and not parsed.password
            )


        print(approved_source("https://go.dev/blog/context"))
        print(approved_source("http://127.0.0.1:8000/private"))
        # 发出请求时还需禁止重定向，限制字节数与超时。
    """,
        r"""
        const hosts = new Set(['go.dev', 'docs.python.org']);
        export function approvedSource(raw: string): boolean {
          try {
            const url = new URL(raw);
            return url.protocol === 'https:' && hosts.has(url.hostname) && !url.username && !url.password;
          } catch {
            return false;
          }
        }
        // fetch 使用 redirect: 'error' 与超时；逐块限制响应大小。
    """,
        r"""
        package main

        import "net/url"

        func approvedSource(raw string) bool {
        	parsed, err := url.Parse(raw)
        	if err != nil {
        		return false
        	}
        	host := parsed.Hostname()
        	return parsed.Scheme == "https" && parsed.User == nil && (host == "go.dev" || host == "docs.python.org")
        }
        func main() {}

        // http.Client 的 CheckRedirect、Timeout 与 io.LimitReader 共同限定出站请求。
    """,
    ),
    "git": example(
        r"""
        ALLOWED_PREFIXES = {"feat", "fix", "docs", "test", "chore"}


        def valid_commit(message):
            prefix, separator, summary = message.partition(": ")
            return bool(separator and prefix in ALLOWED_PREFIXES and summary.strip())


        print(valid_commit("feat: 增加资料检索接口"))
        print(valid_commit("随便改改"))
        # main 保存验收版本，develop 承载小步开发；main 不强推。
    """,
        r"""
        const prefixes = new Set(['feat', 'fix', 'docs', 'test', 'chore']);
        export function validCommit(message: string) {
          const separator = message.indexOf(': ');
          return (
            separator > 0 &&
            prefixes.has(message.slice(0, separator)) &&
            !!message.slice(separator + 2).trim()
          );
        }
        // CI 冻结 pnpm lockfile，执行格式、类型、单测、浏览器和构建检查。
    """,
        r"""
        package main

        import "strings"

        func validCommit(message string) bool {
        	prefix, summary, found := strings.Cut(message, ": ")
        	if !found || strings.TrimSpace(summary) == "" {
        		return false
        	}
        	switch prefix {
        	case "feat", "fix", "docs", "test", "chore":
        		return true
        	}
        	return false
        }
        func main() {}
    """,
    ),
    "vercel": example(
        r"""
        import os
        from fastapi import FastAPI

        app = FastAPI()


        @app.get("/api/health")
        def health():
            # 只公开能力是否配置，不返回环境变量值。
            return {"status": "ok", "model_configured": bool(os.getenv("DEEPSEEK_API_KEY"))}


        # api/index.py 导出 app；平台托管 secrets；构建后验证 /api/health。
    """,
        r"""
        import { Hono } from 'hono';
        const app = new Hono();
        app.get('/api/health', (c) =>
          c.json({ status: 'ok', modelConfigured: !!process.env.DEEPSEEK_API_KEY }),
        );
        export default app;
        // Hono 按官方 Vercel adapter 入口部署；环境变量仅在服务端读取。
        // 预览验收后再发布生产，记录 deployment URL 与 commit。
    """,
        r"""
        package handler

        import (
        	"encoding/json"
        	"net/http"
        	"os"
        )

        func Handler(w http.ResponseWriter, r *http.Request) {
        	if r.URL.Path != "/api/health" {
        		http.NotFound(w, r)
        		return
        	}
        	w.Header().Set("Content-Type", "application/json")
            json.NewEncoder(w).Encode(map[string]any{"status": "ok", "model_configured": os.Getenv("DEEPSEEK_API_KEY") != ""})
        }

        // api/health.go 按 Go 函数入口组织；部署后验证 API 与前端。
    """,
    ),
    "observability": example(
        r"""
        import time
        from uuid import uuid4


        def observe(stage, operation):
            start = time.monotonic()
            try:
                result = operation()
                status = "success"
            except TimeoutError:
                result, status = None, "timeout"
            return result, {
                "request_id": str(uuid4()),
                "stage": stage,
                "status": status,
                "duration_ms": (time.monotonic() - start) * 1000,
            }


        print(observe("retrieve", lambda: {"count": 2}))
        # 日志不收集完整 prompt、密钥、Cookie 或原始认证状态。
    """,
        r"""
        export async function observe<T>(stage: string, operation: () => Promise<T>) {
          const start = performance.now();
          try {
            const result = await operation();
            return {
              result,
              metrics: {
                requestId: crypto.randomUUID(),
                stage,
                status: 'success',
                durationMs: performance.now() - start,
              },
            };
          } catch {
            return {
              result: null,
              metrics: {
                requestId: crypto.randomUUID(),
                stage,
                status: 'failed',
                durationMs: performance.now() - start,
              },
            };
          }
        }
    """,
        r"""
        package main

        import "time"

        type Metric struct {
        	Stage, Status string
        	Duration      time.Duration
        }

        func observe(stage string, operation func() error) Metric {
        	started := time.Now()
        	status := "success"
        	if err := operation(); err != nil {
        		status = "failed"
        	}
        	return Metric{Stage: stage, Status: status, Duration: time.Since(started)}
        }
        func main() {}
    """,
    ),
    "product": example(
        r"""
        from dataclasses import dataclass


        @dataclass(frozen=True)
        class Story:
            actor: str
            action: str
            success: str
            failure: str


        def first_slice():
            return Story("学习者", "提交一个资料问题", "回答包含可打开的引用", "无证据时明确提示")


        print(first_slice())
        # 先联通页面、API、检索与失败状态；再扩展认证与跨设备同步。
    """,
        r"""
        type Story = { actor: string; action: string; success: string; failure: string };
        export function firstSlice(): Story {
          return {
            actor: '学习者',
            action: '提交一个资料问题',
            success: '回答包含可打开的引用',
            failure: '无证据时明确提示',
          };
        }
        // 每条用户故事给出可观察结果，避免用“完成页面”代替产品验收。
    """,
        r"""
        package main

        import "fmt"

        type Story struct{ Actor, Action, Success, Failure string }

        func firstSlice() Story {
        	return Story{"学习者", "提交一个资料问题", "回答包含可打开的引用", "无证据时明确提示"}
        }
        func main() { fmt.Println(firstSlice()) }
    """,
    ),
    "integration": example(
        r"""
        from typing import Protocol


        class Repository(Protocol):
            def search(self, user: str, query: str) -> list[dict]: ...


        async def ask(user, query, repository: Repository, provider):
            documents = repository.search(user, query)
            if not documents:
                return {"answer": "没有可引用的证据", "citations": []}
            result = await provider.generate(query, [d["text"] for d in documents])
            return {"answer": result["answer"], "citations": [d["url"] for d in documents]}


        # 测试注入只读 repository 和 fake provider；真实适配器另行验收。
    """,
        r"""
        type Document = { text: string; url: string };
        type Dependencies = {
          search: (user: string, query: string) => Promise<Document[]>;
          generate: (query: string, context: string[]) => Promise<string>;
        };
        export async function ask(user: string, query: string, deps: Dependencies) {
          const docs = await deps.search(user, query);
          if (!docs.length) return { answer: '没有可引用的证据', citations: [] };
          return {
            answer: await deps.generate(
              query,
              docs.map((d) => d.text),
            ),
            citations: docs.map((d) => d.url),
          };
        }
    """,
        r"""
        package main

        import "context"

        type Document struct{ Text, URL string }
        type Dependencies interface {
        	Search(context.Context, string, string) ([]Document, error)
        	Generate(context.Context, string, []Document) (string, error)
        }

        func ask(ctx context.Context, user, query string, deps Dependencies) (string, []Document, error) {
        	docs, err := deps.Search(ctx, user, query)
        	if err != nil {
        		return "", nil, err
        	}
        	if len(docs) == 0 {
        		return "没有可引用的证据", docs, nil
        	}
        	answer, err := deps.Generate(ctx, query, docs)
        	return answer, docs, err
        }
        func main() {}
    """,
    ),
    "launch": example(
        r"""
        def release_evidence(evidence):
            required = {
                "api_health",
                "browser_flow",
                "secrets_managed",
                "rollback_tested",
                "cost_limit_set",
            }
            missing = sorted(key for key in required if evidence.get(key) is not True)
            return {"ready": not missing, "missing": missing}


        print(release_evidence({"api_health": True, "browser_flow": True}))
        # 记录实际 URL、commit 和验证结果；未配置的能力不能标为通过。
    """,
        r"""
        export function releaseEvidence(evidence: Record<string, boolean>) {
          const required = ['apiHealth', 'browserFlow', 'secretsManaged', 'rollbackTested', 'costLimitSet'];
          const missing = required.filter((key) => evidence[key] !== true);
          return { ready: missing.length === 0, missing };
        }
        // 发布与验收分别保存证据；模型 mock 通过不代表真实供应商已连通。
    """,
        r"""
        package main

        import "fmt"

        func releaseEvidence(evidence map[string]bool) []string {
        	missing := []string{}
        	for _, key := range []string{"api_health", "browser_flow", "secrets_managed", "rollback_tested", "cost_limit_set"} {
        		if !evidence[key] {
        			missing = append(missing, key)
        		}
        	}
        	return missing
        }
        func main() { fmt.Println(releaseEvidence(map[string]bool{"api_health": true})) }
    """,
    ),
}
