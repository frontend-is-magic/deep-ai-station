import io
import zipfile

from scripts.check_secrets import audit, scan


def test_sensitive_paths_are_rejected_but_blank_template_is_allowed():
    for name in [
        ".env.local",
        "backend/credentials.json",
        ".aws/config",
        "x/server.pem",
        "x/auth-state.json",
    ]:
        assert "sensitive-path" in audit(name, b"")
    assert (
        audit(
            ".env.example",
            b'DEEPSEEK_API_KEY=\nPLAYGROUND_ACCESS_TOKEN=""\nDEEPSEEK_MODEL=deepseek-flash',
        )
        == []
    )
    assert audit(".env.example", b"DEEPSEEK_API_KEY=placeholder") == ["nonempty-secret-template"]


def test_key_detection_returns_only_rule_ids():
    for payload, rule in [
        (b"sk-" + b"a" * 32, "provider-key"),
        (b"ghp_" + b"b" * 36, "github-token"),
        (b"-----BEGIN " + b"RSA PRIVATE KEY-----", "private-key"),
        (b"AKIA" + b"A" * 16, "aws-access-id"),
    ]:
        assert audit("config.txt", payload) == [rule]
    assert audit("demo.txt", b"test-only; DEEPSEEK_API_KEY; sk-placeholder") == []


def test_zip_contents_are_scanned():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("backend/.env", b"DEEPSEEK_API_KEY=")
        archive.writestr("frontend/main.ts", b"export {}")
    assert scan("starter.zip", buffer.getvalue()) == [
        ("starter.zip!backend/.env", "sensitive-path")
    ]
