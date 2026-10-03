# 会话与授权实验

为“认证”和“应用安全”两课提供可直接运行的 Python / FastAPI、TypeScript / Hono、Go / Gin 版本。[共同契约](CONTRACT.md)固定身份、权限与错误边界；`fixtures.json` 只含公开假会话和假资料，无需真实账号、模型或费用。默认只监听127.0.0.1:8022；不要把这些假会话部署到公网。重启会重建内存会话，已撤销的教学凭据也会重新初始化。

## 启动选定语言

从解包根目录执行，只需运行所下载语言的一组命令。多个版本不要同时占用同一端口；可通过PORT选择其他本机端口。

### Python 3.12 / uv

```sh
uv sync --locked
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest -q
uv run --frozen python app.py
```

### Node.js 24 / pnpm 10.32.1

```sh
pnpm install --frozen-lockfile
pnpm check
pnpm start
```

### Go 1.27.1

```sh
go test -mod=readonly -race ./...
go run -mod=readonly .
```

每个包包含自己的锁文件、固定资料、共同案例、测试与格式配置，解包后不需要教程仓库。学习者修改仅在自己的受控开发环境或隔离沙箱执行，学习平台不会运行上传的服务代码。

## 先观察身份与资源边界

下列值全是包内公开假值，不能替换成真实凭据写入脚本或课程笔记。

```sh
curl -i http://127.0.0.1:8022/me
curl -i http://127.0.0.1:8022/me -H 'Authorization: Bearer lab-alice-session'
curl -i http://127.0.0.1:8022/documents -H 'Authorization: Bearer lab-alice-session'
curl -i http://127.0.0.1:8022/documents/bob-notes -H 'Authorization: Bearer lab-alice-session'
curl -i -X PATCH http://127.0.0.1:8022/documents/alice-notes -H 'Authorization: Bearer lab-alice-readonly-session' -H 'Content-Type: application/json' --data '{"archived":true}'
```

依次应看到401、Alice的服务端身份、仅Alice资料、他人资料404、只读身份403。把最后一个请求换成Bob的资料仍应404；查看自己的资料权限不足才是403，不能借错误区分他人资料是否存在。

## 认证课：从会话到可信身份

1. 找到SessionStore查找与Principal构建。改X-User-Id、owner查询或role正文不能改变服务端身份。认证失败不能调用资料仓储。
2. 比较缺失、未知、过期与撤销：均401，不泄漏是哪一种凭据状态。Clock由测试注入，精确验证now等于到期时间的拒绝，不靠等待一分钟猜结果。
3. 用Alice的假会话POST `/logout`，JSON正文为`{}`。再次访问`/me`应401，但`lab-alice-second-session`和Bob仍有效。
4. 阅读慢正文测试：初次认证后暂停上传，另一请求注销；上传完成前重新检查会话，写入应401且资料不变。读正文期间不能占用全局会话锁；最终检查后已准入的操作仍可能完成。
5. 在练习副本暂时删掉Repository的owner条件，确认跨用户用例失败；恢复后重跑。只在隔离的假资料练习中做这个负对照。

## 安全课：Cookie与CSRF

```sh
curl -i -X PATCH http://127.0.0.1:8022/documents/alice-notes -H 'Cookie: __Host-lab_session=lab-alice-session' -H 'Content-Type: application/json' --data '{"archived":true}'
curl -i -X PATCH http://127.0.0.1:8022/documents/alice-notes -H 'Cookie: __Host-lab_session=lab-alice-session' -H 'Origin: https://lab.example.test' -H 'X-CSRF-Token: lab-csrf-a' -H 'Content-Type: application/json' --data '{"archived":true}'
```

第一条403，第二条200。依次去掉Origin、换成相似域名、改成Bob的CSRF值，应全部拒绝且不修改资料。Authorization与会话Cookie同时存在、重复认证字段也不能回退到另一身份。

这里curl手动发送Cookie头，只验证服务端校验。真实浏览器是否存储/发送`__Host-`、Secure、HttpOnly、SameSite=Strict的Cookie，必须在HTTPS环境另外验收；没有假登录接口。本实验不实现密码、JWT签名、OIDC、账户找回或生产会话发行。SameSite/HttpOnly是补充属性，不能代替服务端CSRF校验。

## 记录你的证据

先运行完整共享与原生测试，比较另一个语言包对相同请求的状态、JSON和响应头。记录自己的源码版本、成功/失败/故障注入及尚未验证事项到[EVIDENCE.md](EVIDENCE.md)，再回课程笔记。不要填写真实会话或认证头；实验通过不自动完成课程。生产需要可信身份提供商、不可预测会话、HTTPS、共享持久撤销、登录防护与独立安全验收。

来源：[Deep AI Station](https://github.com/frontend-is-magic/deep-ai-station)。参考资料见CONTRACT.md。
