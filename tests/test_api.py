"""HTTP 接口测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_segments_endpoint_success():
    resp = client.post(
        "/segments",
        json={
            "words": [
                {"start_ms": 0, "end_ms": 2000, "text": "你好。", "speaker": "A"},
                {"start_ms": 2000, "end_ms": 4000, "text": "再见", "speaker": "B"},
            ]
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert [s["text"] for s in data["segments"]] == ["你好。", "再见"]
    assert data["total_score"] == 0  # 两段均为 2000ms 整且以句末标点结尾
    assert "1\n00:00:00,000 --> 00:00:02,000\n[A] 你好。" in data["srt"]
    assert data["segments"][0]["start_word_index"] == 1
    assert data["segments"][0]["end_word_index"] == 1


def test_segments_endpoint_overlap_422():
    resp = client.post(
        "/segments",
        json={
            "words": [
                {"start_ms": 0, "end_ms": 1500, "text": "甲", "speaker": "A"},
                {"start_ms": 1000, "end_ms": 2500, "text": "乙", "speaker": "A"},
            ]
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "time_overlap"
    assert detail["word_index"] == 2


def test_segments_endpoint_infeasible_422():
    resp = client.post(
        "/segments",
        json={
            "words": [
                {"start_ms": 0, "end_ms": 6500, "text": "长篇独白", "speaker": "A"},
            ]
        },
    )
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "no_feasible_segmentation"
    assert detail["earliest_unreachable"] == 1


def test_segments_endpoint_empty_words_422():
    resp = client.post("/segments", json={"words": []})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_request"
