# 可运行课程实验

当前实验为 `api-contract-v1`，对应全栈路线的“路由与依赖注入”和“输入校验”。三个下载包分别使用 Python / FastAPI、TypeScript / Hono、Go / Gin，共享固定资料和 HTTP 契约。课程页按当前所选语言提供下载，原有参考片段练习资料继续保留。

学习者的启动命令、curl 请求和练习步骤见 [实验 README](api-contract/shared/README.md)，响应规则见 [共同契约](api-contract/shared/CONTRACT.md)。每个 ZIP 都包含这些说明、证据模板、源码、测试、依赖锁文件、.gitignore 与独立格式配置；不需要模型凭据。

## 维护与验证

- `api-contract/shared/`：共同资料、请求/响应案例、契约、学习说明与证据模板。
- `api-contract/python/`、`typescript/`、`go/`：各自完整入口、路由、服务、可注入 Repository 与测试。
- `api-contract/manifest.json`：实验版本、课时与支持语言。
- `public/labs/`：生成的确定性 ZIP，固定文件白名单，不包含本地缓存或依赖目录。

```sh
python3 scripts/build_course_labs.py
python3 scripts/build_course_labs.py --check
python3 scripts/verify_course_labs.py
# 单独验证一种语言
python3 scripts/verify_course_labs.py --language go
```

验证环境需 Python 3.12 / uv、Node.js 24 / pnpm 10.32.1、Go 1.27.1。脚本将包解压到仓库之外，使用冻结依赖运行格式、类型与行为测试，再启动实际 HTTP 监听验证共同案例；请求固定为本机回环地址，进程结束后确认端口释放。测试子进程不接收模型密钥。CI 分三种语言独立执行同样检查，额外 headless 页面测试核对两课的语言选择、实际下载与窄屏显示。

实验只使用维护者固定资料，不调用模型、数据库或外部 URL。学习者应在独立练习环境运行修改，平台 API 不执行这些输入。通过固定案例不等于新增业务已验收，真实结果应填写包内 `EVIDENCE.md`。
