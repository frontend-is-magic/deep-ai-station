"""Optional E2B template build. No credentials enter the template or build logs."""

import os
import sys

from e2b import Template

ALIAS = "deep-ai-station-go-v1"
GO_FILE = "go1.27.1.linux-amd64.tar.gz"
GO_SHA256 = "63d339f0da5ab53635a56f2490a7984dfe12dfcff22ad749f63edaf590168445"


def template():
    return (
        Template()
        .from_template("code-interpreter-v1")
        .run_cmd(
            [
                f"curl -fsSL https://go.dev/dl/{GO_FILE} -o /tmp/go.tar.gz",
                f"echo '{GO_SHA256}  /tmp/go.tar.gz' | sha256sum --check",
                "tar -C /usr/local -xzf /tmp/go.tar.gz && rm /tmp/go.tar.gz",
                "ln -s /usr/local/go/bin/go /usr/local/bin/go",
            ],
            user="root",
        )
        .run_cmd(
            [
                "go version",
                "mkdir -p /tmp/go-cache",
                'printf \'package main\\nimport (_ "fmt"; _ "encoding/json"; _ "net/http"; _ "context"; _ "database/sql"; _ "testing")\\nfunc main() {}\\n\' > /tmp/warm.go',
                "GOTOOLCHAIN=local GOCACHE=/tmp/go-cache go build -o /tmp/warm /tmp/warm.go",
                "rm /tmp/warm /tmp/warm.go",
            ],
            user="user",
        )
    )


if __name__ == "__main__":
    if "--build" not in sys.argv:
        print(
            "Prepared trusted Go template recipe. Pass --build only after E2B setup and budget confirmation."
        )
    elif not os.getenv("E2B_API_KEY"):
        raise SystemExit("Configure E2B_API_KEY through a protected environment first")
    else:
        try:
            info = Template.build(template(), alias=ALIAS, cpu_count=1, memory_mb=1024)
            print(f"Template built: {info.template_id}; alias: {ALIAS}")
        except Exception:
            raise SystemExit(
                "Template build failed; inspect the E2B dashboard without posting credentials"
            ) from None
