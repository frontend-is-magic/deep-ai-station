"""Small lexical course search; not an embedding or vector database."""

import re
from typing import Literal, TypedDict

from backend.curriculum import LESSONS

STOP = {"如何", "怎样", "什么", "一个", "应该", "可以", "需要", "进行", "以及"}
SearchStrategy = Literal["title", "weighted"]


class ScoredLesson(TypedDict):
    lesson: dict
    score: int
    matched_terms: list[str]


def tokens(query: str) -> set[str]:
    result = set(re.findall(r"[a-z][a-z0-9_.-]+", query.lower()))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", query):
        result.add(phrase)
        result.update(phrase[i : i + 2] for i in range(len(phrase) - 1))
    return set(sorted(result - STOP, key=lambda word: (-len(word), word))[:64])


def retrieve_scored(
    query: str,
    track: str | None = None,
    limit: int = 3,
    strategy: SearchStrategy = "weighted",
) -> list[ScoredLesson]:
    """Score course fields only; ties retain curriculum order, as in retrieve."""
    if strategy not in ("title", "weighted"):
        raise ValueError("Unknown course search strategy")
    words = tokens(query)
    if not words:
        return []
    scored: list[ScoredLesson] = []
    for lesson in LESSONS.values():
        if track and lesson["track"] != track:
            continue
        title = lesson["title"].lower()
        objective = lesson["objective"].lower()
        body = " ".join(lesson["body"]).lower()
        term_scores = {
            word: (
                int(word in title)
                if strategy == "title"
                else 4
                if word in title
                else 2
                if word in objective
                else 1
                if word in body
                else 0
            )
            for word in words
        }
        score = sum(term_scores.values())
        if score:
            scored.append(
                {
                    "lesson": lesson,
                    "score": score,
                    "matched_terms": sorted(word for word, value in term_scores.items() if value),
                }
            )
    scored.sort(key=lambda item: -item["score"])
    return scored[:limit]


def retrieve(query: str, track: str | None = None, limit: int = 3) -> list[dict]:
    return [item["lesson"] for item in retrieve_scored(query, track, limit)]
