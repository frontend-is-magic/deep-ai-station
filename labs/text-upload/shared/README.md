# 受限文本上传实验

用 FastAPI、Hono、Gin 实现同一上传边界：原始UTF-8正文、字节上限、严格显示名、可信owner、原子配额、私有附件下载。ZIP包含一门语言完整源码、冻结依赖、契约和测试。无需密钥、模型或外部存储；公开假Bearer仅限本地教学。

## 独立启动

```sh
# Python 3.12 / uv
uv sync --locked
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest -q
uv run --frozen python app.py

# Node.js 24 / pnpm 10.32.1
pnpm install --frozen-lockfile
pnpm check
pnpm start

# Go 1.27.1
 go test -mod=readonly -race ./...
 go run -mod=readonly .
```

三种入口均默认 `127.0.0.1:8023`，一次启动一种；可用PORT选择空闲端口。只运行ZIP里自己的语言命令。停止自己启动的进程；平台不会代执行修改后的代码。

```sh
curl --noproxy '*' http://127.0.0.1:8023/health
curl --noproxy '*' -X POST http://127.0.0.1:8023/documents \
  -H 'Authorization: Bearer lab-alice-session' \
  -H 'Content-Type: text/plain; charset=utf-8' -H 'X-Filename: notes.txt' \
  --data-binary '公开教学文本'
curl --noproxy '*' http://127.0.0.1:8023/documents \
  -H 'Authorization: Bearer lab-alice-session'
```

先比较一次有效上传和失败后的列表，再以Bob假会话读取Alice文档及内容，检查统一404。上传4096/4097字节、畸形UTF-8、路径式文件名和同名文件，观察真实字节计数和独立ID。通过GET文档ID/content核对原字节及attachment/nosniff头。最后用原生测试观察正文后会话重检、仓储故障和并发配额。

把实际结果写入EVIDENCE.md、课程笔记和语言实践记录，不自动完成课程。先学会话授权实验再扩展认证；本实验不支持Cookie认证，也不等价于生产上传：没有持久化、PDF/Markdown解析、杀毒、文件系统权限、分布式配额、解析器沙箱、模型或RAG索引。Markdown与HTML样式文本被当作字节下载，不渲染执行。生产扩展需要逐项独立设计和验收。

详见[共同契约](CONTRACT.md)；contract-cases.json为固定公开案例，不含用户文档或秘密。
