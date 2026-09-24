"""FastAPI 入口：POST /segments 字幕分段服务。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .errors import DomainError
from .schemas import SegmentRequest, SegmentResponse
from .segmentation import optimal_segmentation
from .srt import render_srt

app = FastAPI(
    title="Podcast Subtitle Segmentation API",
    description="播客转写后处理：在硬约束下用动态规划求最优字幕分段，返回 JSON 与 SRT。",
    version="1.0.0",
)


def _error_response(code: str, message: str, extra: dict | None = None):
    detail = {"code": code, "message": message}
    if extra:
        detail.update(extra)
    return JSONResponse(status_code=422, content={"error": detail})


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError):
    return _error_response(exc.code, exc.message, exc.extra)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    return _error_response(
        "invalid_request",
        "请求体不合法：words 必须为非空数组，每项含非负整数 start_ms/end_ms、"
        "非空字符串 text/speaker",
        {"errors": exc.errors()},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/segments", response_model=SegmentResponse)
async def create_segments(request: SegmentRequest):
    words = request.words  # Pydantic 模型自带所需属性
    segments, total_score = optimal_segmentation(words)
    return SegmentResponse(
        segments=segments,
        total_score=total_score,
        srt=render_srt(segments),
    )
