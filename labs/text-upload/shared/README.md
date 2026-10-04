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

## 用共享 React 界面操作

三个语言 ZIP 都带同一份 `client/`。先保留上面的后端进程，在第二个终端进入解压目录，使用 Node.js 24 / pnpm 10.32.1：

```sh
pnpm --dir client install --frozen-lockfile
pnpm --dir client check
pnpm --dir client dev
```

打开 `http://127.0.0.1:5197`。界面固定代理本机 `8023`；若后端使用其他 `PORT`，客户端启动时设置相同的 `LAB_API_PORT`，例如 `LAB_API_PORT=18023 pnpm --dir client dev`。端口仅允许数字，不填写远端地址或真实凭据。完成后停止两个自有进程。

在“教学身份”中选择 Alice，用“选择文本文件”选一份 `notes.txt` 或 `notes.md`，再点击“上传文件”。文件名仅限 ASCII 字母、数字、短横线和下划线；正文可包含中文、emoji、CRLF与组合字符，每份最多4096字节。界面保留原始字节，按扩展名确定媒体类型，不把文件改写成字符串后上传。先观察真实201的ID、字节数和SHA，再从“我的文档”查看纯文本或下载原始附件；可把 `<script>` 和 Markdown 链接作为原文核对，它们不会被渲染或执行。

切换 Bob 后 Alice 的文件、列表和预览立即清除；只读 Alice 可以回读、不能从界面提交。过期和撤销身份由真实后端拒绝。列表只显示最近观察到的计数和字节，配额裁定仍由服务端完成；同名上传创建新ID。运行 memory 还是 SQLite 取决于后端启动命令，界面不根据健康响应推断耐久保存。用 SQLite 重启流程核对原文时，点击“刷新我的列表”再下载。

### 上传结果未确认时

点击“停止等待”、断连、超时或收到提交未确认响应，不能撤销已经发生的服务端写入。界面显示“服务器可能已保存”，保留只读查询，但不自动重传。先刷新并下载核对；相同文件名、字节和SHA也无法唯一证明某次POST是否成功。

如果决定再次上传，显式点击“我已核对，允许再次上传（可能重复）”，重新选择文件，再点击上传。该动作仅允许一个新POST，不会立即发送，也不清除旧未知提示；新201确认的是新请求。切换同owner另一会话仍保留提示，切到Bob不显示Alice的私有反馈。本页仅在内存保存这些状态，刷新页面会丢失未知提示；本实验没有跨页面恢复或幂等保证。文件、预览、身份与反馈不写浏览器存储、URL或日志。

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
