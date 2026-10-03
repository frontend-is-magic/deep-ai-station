"""固定教学资料仓储；不接受用户代码，不访问网络。"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def ascii_lower(value: str) -> str:
    """契约仅忽略 ASCII 英文字母大小写，其他 Unicode 码点保留。"""
    return value.translate(ASCII_LOWER)


def fixture_path(name: str) -> Path:
    """独立下载包的资料在同目录，源码中的资料由三种语言共享。"""
    local = Path(__file__).with_name(name)
    return local if local.is_file() else local.parent.parent / "shared" / name


@dataclass(frozen=True)
class Lesson:
    id: str
    title: str
    body: str


class Repository(Protocol):
    def find(self, lesson_id: str) -> Lesson | None: ...

    def search(self, question: str) -> list[Lesson]: ...


class FixedRepository:
    def __init__(self, lessons: list[Lesson]):
        self._lessons = tuple(lessons)

    @classmethod
    def from_fixture(cls) -> "FixedRepository":
        data = json.loads(fixture_path("lessons.json").read_text(encoding="utf-8"))
        return cls([Lesson(**item) for item in data])

    def find(self, lesson_id: str) -> Lesson | None:
        return next((lesson for lesson in self._lessons if lesson.id == lesson_id), None)

    def search(self, question: str) -> list[Lesson]:
        query = ascii_lower(question)
        return [
            lesson
            for lesson in self._lessons
            if query in ascii_lower(lesson.title) or query in ascii_lower(lesson.body)
        ]
