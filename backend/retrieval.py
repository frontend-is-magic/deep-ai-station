"""Small lexical course search; not an embedding or vector database."""

import re

from backend.curriculum import LESSONS

STOP = {"如何", "怎样", "什么", "一个", "应该", "可以", "需要", "进行", "以及"}


def tokens(query: str) -> set[str]:
    result = set(re.findall(r"[a-z][a-z0-9_.-]+", query.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", query):
        result.add(phrase)
        result.update(phrase[i : i + 2] for i in range(len(phrase) - 1))
    return set(sorted(result - STOP, key=lambda word: (-len(word), word))[:64])


def retrieve(query: str, track: str | None = None, limit: int = 3) -> list[dict]:
    words = tokens(query)
    if not words:
        return []
    scored = []
    for lesson in LESSONS.values():
        if track and lesson["track"] != track:
            continue
        title = lesson["title"].lower()
        objective = lesson["objective"].lower()
        body = " ".join(lesson["body"]).lower()
        score = sum(
            4 if word in title else 2 if word in objective else 1 if word in body else 0
            for word in words
        )
        if score:
            scored.append((score, lesson))
    scored.sort(key=lambda pair: -pair[0])
    return [lesson for _, lesson in scored[:limit]]
