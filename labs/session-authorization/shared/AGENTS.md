# 会话与授权实验

- 以 CONTRACT.md 和共同案例为三语言行为约定；先运行基线，再验证修改的成功和失败路径。
- uv / FastAPI / Ruff、pnpm / Hono / Prettier、Go / Gin / gofmt；冻结依赖并保留锁文件。
- fixtures.json 只含公开教学假会话。不得导入真实身份或部署这些假会话；入口只监听回环地址。
- 身份来自服务端会话库，owner由Principal传入Repository；拒绝凭据歧义，Cookie写操作验证Origin与CSRF。
- 用户代码只在独立受控练习环境或隔离沙箱运行。main稳定、develop开发，提交前缀加中文，不强推main。
- 敏感配置、真实Cookie/令牌、私钥、认证缓存、日志和数据库不得提交；遵守.gitignore。
- 实际命令、状态和未验证项写EVIDENCE.md，不保存请求认证头，不把本地HTTP当作生产HTTPS验收。
