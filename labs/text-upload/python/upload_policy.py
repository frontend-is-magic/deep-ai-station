"""上传和持久资料共用的固定文件/正文校验。"""

import re

from errors import LabError

MAX_BODY_BYTES = 4096
MEDIA = re.compile(r'text/(plain|markdown)(?:[ \t]*;[ \t]*charset[ \t]*=[ \t]*(?:utf-8|"utf-8"))?')
FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,59}\.(?i:txt|md)", re.ASCII)


def ascii_lower(value: str) -> str:
    return value.translate(
        str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    )


def validate_text(body: bytes) -> None:
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeError:
        raise LabError(422, "invalid_text") from None
    if not text.strip(" \t\r\n") or any(
        (ord(char) < 32 and char not in "\t\r\n") or 127 <= ord(char) <= 159 or char == "\ufeff"
        for char in text
    ):
        raise LabError(422, "invalid_text")
