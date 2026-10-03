# 隔离代码实验

用户代码只进入独立 E2B 环境；应用主机不执行或编译用户提交内容。

## 配置与能力

服务端托管 `E2B_API_KEY`、`PLAYGROUND_ACCESS_TOKEN` 并配置 [共享请求配额](shared-quota.md) 后，Python 与 TypeScript 隔离运行才启用。Go 还要求 `E2B_GO_TEMPLATE` 指向预装 Go 编译器的受信模板。界面根据 `/api/capabilities` 展示实际配置状态；访问码只保留在页面内存。

默认 `code-interpreter-v1` 提供 Python/TypeScript 运行环境。框架示例依赖、数据库驱动和测试工具需要预先放进模板。出站网络关闭后，实验不能临时下载依赖。Go 片段需要 `package main` 和 `main()` 才能按当前单文件命令运行；单元测试片段应在独立练习项目使用 `go test`。

Go 受信模板配方位于 `scripts/build_go_template.py`，继承官方 Code Interpreter 模板，安装 Go 1.27.1 并验证官方 SHA-256。配方限制 1 CPU / 1024 MB，不包含应用密钥。主线尚未实际构建或运行收费沙箱。

```sh
# 只检查配方，不调用 E2B
uv run python scripts/build_go_template.py
# 在已配置受保护环境、确认服务费用上限后，可构建
uv run python scripts/build_go_template.py --build
```

构建成功后，将模板 alias 或 ID 配置为 `E2B_GO_TEMPLATE`。部署与实际验收前，分别验证 `print(42)`、TypeScript 输出、Go 输出、死循环超时和失败输出。服务账号配置与费用限制由独立人工配置对话集中处理，不在聊天中提交密钥，也不自动购买额度。

## 请求与运行边界

- 输入上限 20,000 字符，HTTP 请求体实际字节上限 50,000，未知字段拒绝。
- 每次创建全新实例，`secure=True`、`allow_internet_access=False`、`envs={}`；应用供应商密钥不会注入沙箱。
- 单次运行 12 秒，获得请求准入后的准备与运行 25 秒，实例最大存活 45 秒；数据库准入另有 5 秒应用截止时间。
- stdout / stderr 返回内容合计最多 20,000 字符；达到上限停止读取并清理实例。
- 单实例服务最多两个并发槽；同一服务器 scope 的共享沙箱配额为固定分钟 2 次、UTC 日 20 次创建准入，与模型配额独立。未配置语言、无效输入、未授权和本地忙碌不消耗额度；准入后创建失败或取消不退款。并发槽仍为单实例保护，生产还需服务商预算及按需配置全局并发限制。
- Go 源码写入固定文件，执行固定命令，用户内容不会拼接进 shell。
- 成功、失败、超时、输出超限、取消都进入清理流程；清理请求失败会如实返回 `expiry-fallback`，等待实例存活上限到期。

`executed` 表示代码已提交隔离运行；Go 编译失败时程序未必进入执行。`passed` 只表示该次运行没有报告错误，不能代替课程业务验收。输出以纯文本展示，不渲染用户生成的 HTML。

## 已验证与未验证

已用 fake SDK 验证网络拒绝、空环境、固定命令、输出截断、超时、取消销毁、错误脱敏和清理失败报告；浏览器使用 mock 验证访问码、纯文本输出与编辑保留。没有使用真实 E2B Key，没有创建真实沙箱，没有以 mock 结果声称生产连通。

参考：[E2B 模板扩展](https://docs.e2b.dev/code-interpreting/customize-template)、[E2B Python SDK](https://e2b.dev/docs/sdk-reference/python-sdk/v2.5.0/sandbox_sync)、[Go 官方下载与校验值](https://go.dev/dl/?mode=json)。
