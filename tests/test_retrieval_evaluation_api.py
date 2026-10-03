"""The public evaluation endpoint accepts only bounded, free course experiments."""

import pytest
from fastapi.testclient import TestClient

from backend.app import app

client = TestClient(app)
REQUEST = {
    "track": "agent",
    "baseline": {"strategy": "title", "top_k": 1},
    "candidate": {"strategy": "weighted", "top_k": 3},
}


def test_evaluation_api_works_without_secrets_and_keeps_tracks_separate(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("PLAYGROUND_ACCESS_TOKEN", raising=False)
    for track in ("agent", "fullstack"):
        response = client.post(
            "/api/playground/retrieval-evaluation", json={**REQUEST, "track": track}
        )
        assert response.status_code == 200
        result = response.json()
        assert result["track"] == track and result["model_calls"] == 0
        assert result["configurations"]["candidate"] == REQUEST["candidate"]
        assert len(result["cases"]) == 12
        for case in result["cases"]:
            for side in ("baseline", "candidate"):
                assert all(item["id"].startswith(track + "-") for item in case[side]["results"])
        assert result["metrics"]["candidate"]["positive_cases"] == 10
        assert result["metrics"]["candidate"]["negative_cases"] == 2


@pytest.mark.parametrize(
    "patch",
    [
        {"track": "external"},
        {"baseline": {"strategy": "vector", "top_k": 2}},
        *(
            {"candidate": {"strategy": "weighted", "top_k": value}}
            for value in (0, 6, True, 3.0, "3")
        ),
        {"candidate": {"strategy": "weighted", "top_k": 3, "threshold": 0}},
        {"query": "任意问题不应扩展固定评测集"},
        {"corpus_url": "https://example.com/private"},
        {"provider": "deepseek"},
    ],
)
def test_evaluation_api_rejects_unbounded_or_unknown_configuration(patch):
    response = client.post("/api/playground/retrieval-evaluation", json={**REQUEST, **patch})
    assert response.status_code == 422
