from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse


app = FastAPI(
    title="OASIS retired claim collection gateway",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "feature": "retired"}


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    response_class=HTMLResponse,
)
async def retired(path: str = "") -> HTMLResponse:
    del path
    return HTMLResponse(
        """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>경정청구 자료수집 종료 안내</title>
  <style>
    body{font-family:system-ui,sans-serif;background:#f5f7fb;margin:0;padding:32px;color:#17345f}
    main{max-width:640px;margin:10vh auto;background:#fff;padding:36px;border-radius:18px;box-shadow:0 12px 36px #17345f1c}
    h1{font-size:24px;margin:0 0 16px}p{line-height:1.7;margin:0}
  </style>
</head>
<body><main><h1>기존 경정청구 자료수집 기능이 종료되었습니다.</h1>
<p>새 신청은 OASIS CRM의 ‘경정청구 영업신청’ 메뉴를 이용해주세요.</p>
</main></body></html>""",
        status_code=410,
        headers={"Cache-Control": "no-store"},
    )
