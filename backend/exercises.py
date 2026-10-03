"""Portable reference bundle for a lesson; no user code is accepted or run."""

import io
import zipfile

from fastapi import HTTPException

from backend.curriculum import LESSONS

FILES = {"python": "example.py", "typescript": "example.ts", "go": "main.go"}


def exercise_bundle(lesson_id: str, language: str) -> bytes:
    lesson = LESSONS.get(lesson_id)
    if not lesson or language not in lesson["snippets"]:
        raise HTTPException(404, "该课时没有对应语言的参考代码")
    reference = "\n".join(f"- {item['title']}：{item['url']}" for item in lesson["resources"])
    steps = "\n".join(f"{index + 1}. {step}" for index, step in enumerate(lesson["steps"]))
    criteria = "\n".join(f"- [ ] {item}" for item in lesson["criteria"])
    readme = f"""# {lesson["title"]}

{lesson["objective"]}

## 参考代码

`{FILES[language]}` 展示本课机制。框架依赖、Database / Provider / Repository 实现由练习项目提供。
此文件不是已经部署的服务；语法校验不能代替类型检查、编译与业务验收。

Python 练习使用 uv 管理环境，框架片段需 FastAPI / Pydantic；TypeScript 使用独立项目安装相应 Hono / React / Jotai 或测试依赖。
Go 的 `package main` 片段可用 `go run main.go`，含测试的片段应改为 `_test.go` 并运行 `go test`；Gin 示例需配置 Go module 依赖。
框架 API 服务需要明确启动入口，接口类片段需注入实现。用户代码只在受控独立环境运行，不将应用密钥注入沙箱。

## 实践步骤

{steps}

## 验收条件

{criteria}

## 官方资料

{reference}

项目源码：https://github.com/frontend-is-magic/deep-ai-station
"""
    evidence = """# 练习记录

- 环境与依赖版本：
- 验证命令：
- 成功输入与实际结果：
- 失败输入与实际结果：
- 超时或取消观察（如适用）：
- 尚未验证的行为：

只记录可复现证据，不记录密钥、Cookie、验证码或原始认证状态。
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(FILES[language], lesson["snippets"][language])
        archive.writestr("README.md", readme)
        archive.writestr("EVIDENCE.md", evidence)
    return buffer.getvalue()
