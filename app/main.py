"""FastAPI 应用：播客转写后处理字幕分段服务。"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .segmenter import (
    InfeasibleError,
    SegmentationError,
    Segment,
    Word,
    build_srt,
    segment_words,
    total_score,
)

app = FastAPI(
    title="Podcast Subtitle Segmentation API",
    description="按硬约束和动态规划最优化，把播客转写词序列切分为字幕分段，并返回 JSON 与 SRT。",
    version="1.0.0",
)


class WordIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={
        "examples": [
            {"start_ms": 0, "end_ms": 500, "text": "你好", "speaker": "A"},
        ],
    })

    start_ms: int = Field(..., ge=0, description="词开始时间（毫秒）")
    end_ms: int = Field(..., ge=0, description="词结束时间（毫秒）")
    text: str = Field(..., min_length=1, description="词文本")
    speaker: str = Field(..., min_length=1, description="说话人标识")


class SegmentRequest(BaseModel):
    words: list[WordIn] = Field(
        ...,
        min_length=1,
        description="按时间升序排列的词；相邻词时间不得倒序或重叠（首尾相接允许）。",
    )


class SegmentOut(BaseModel):
    index: int
    start_ms: int
    end_ms: int
    duration_ms: int
    text: str
    speaker: str
    char_count: int
    start_word_index: int
    end_word_index: int
    cost: int


class SegmentResponse(BaseModel):
    segments: list[SegmentOut]
    srt: str
    total_score: int


def _error(code: str, message: str, status_code: int = 422, **extra: object) -> JSONResponse:
    detail: dict[str, object] = {"code": code, "message": message}
    detail.update(extra)
    return JSONResponse(status_code=status_code, content={"detail": detail})


@app.exception_handler(RequestValidationError)
async def _validation_exception_handler(_request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder({"detail": {"code": "invalid_request", "errors": exc.errors()}}),
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/segments", response_model=SegmentResponse)
def create_segments(req: SegmentRequest) -> JSONResponse | SegmentResponse:
    words = [
        Word(start_ms=w.start_ms, end_ms=w.end_ms, text=w.text, speaker=w.speaker)
        for w in req.words
    ]
    try:
        segments = segment_words(words)
    except SegmentationError as exc:
        return _error(
            exc.code,
            exc.message,
            word_index=exc.word_index,
        )
    except InfeasibleError as exc:
        return _error(
            "no_feasible_segmentation",
            "no feasible segmentation exists under the length / duration / speaker constraints",
            earliest_unreachable=exc.earliest_unreachable,
        )

    payload = [
        SegmentOut(
            index=s.index,
            start_ms=s.start_ms,
            end_ms=s.end_ms,
            duration_ms=s.duration_ms,
            text=s.text,
            speaker=s.speaker,
            char_count=s.char_count,
            start_word_index=s.start_word_index,
            end_word_index=s.end_word_index,
            cost=s.cost,
        )
        for s in segments
    ]
    return SegmentResponse(
        segments=payload,
        srt=build_srt(segments),
        total_score=total_score(segments),
    )
