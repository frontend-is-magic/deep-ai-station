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

## 让原文与配额跨重启保留

零参数启动仍使用 memory，重启清空。要练习耐久存储，在当前实验目录选择本语言的命令；首次初始化成功后再启动：

```sh
# Python
uv run --frozen python app.py init
uv run --frozen python app.py serve --storage sqlite

# TypeScript：先运行前面的 pnpm check 完成构建
node dist/server.js init
node dist/server.js serve --storage sqlite

# Go
go run -mod=readonly . init
go run -mod=readonly . serve --storage sqlite
```

唯一数据库是启动目录下 `.data/uploads.sqlite3`。已有文件时 init 拒绝，服务缺库时也拒绝；不要为了让命令通过而覆盖原资料。数据库、事务旁文件与本地证据均不提交。公开假会话每次启动重建，数据库只接受 Alice/Bob；它不保存真实账号或共享撤销状态。

先上传中文、CRLF和emoji原文，保存201中的ID与SHA。停止自己启动的进程，再启动 SQLite 模式，下载原ID并比较原始字节；原owner配额继续生效，Bob读Alice仍404。同名再次上传创建新ID，不覆盖旧文档。三语言使用同一schema，可在服务完全关闭后让另一语言读取同一库；独立ZIP里的原生测试另验证事务、身份和错误边界。

写锁不等待：竞争可能得到503 repository_unavailable；库中不会留下半份上传。503 result_unconfirmed、超时或断连可能发生在提交之后，请先查询自己的列表并核对下载，不能据此判断没有保存；重复POST可能再次创建文档，没有自动重试或幂等保证。同步小文件I/O仍会占用运行线程，不代表生产吞吐或完全非阻塞。

把实际结果写入EVIDENCE.md、课程笔记和语言实践记录，不自动完成课程。先学会话授权实验再扩展认证；本实验不支持Cookie认证、PDF/Markdown解析、杀毒、分布式配额、解析器沙箱、模型或RAG索引。Markdown与HTML样式文本被当作字节下载，不渲染执行。SQLite只验证同机教学库，不代表托管数据库或对象存储已验收。

详见[HTTP共同契约](CONTRACT.md)与[存储扩展](STORAGE.md)；contract-cases.json为固定公开案例，不含用户文档或秘密。
