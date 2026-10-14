"""Точка входа API: uvicorn gosdoc.main:app"""

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from gosdoc.api import admin, auth, files
from gosdoc.core.config import get_settings
from gosdoc.core.health import CRITICAL, check_dependencies
from gosdoc.core.logging import setup_logging


def create_app() -> FastAPI:
    s = get_settings()
    setup_logging(s.log_level)
    app = FastAPI(
        title="ГосДок Ассистент",
        # Документация API — только вне прода; ресурсы Swagger UI грузятся с CDN.
        docs_url=None,
        redoc_url=None,
        openapi_url=None if s.environment == "prod" else "/api/openapi.json",
    )
    for r in (auth.router, admin.router, files.router):
        app.include_router(r)

    @app.get("/health/live", include_in_schema=False)
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", include_in_schema=False)
    def ready() -> JSONResponse:
        deps = check_dependencies(s)
        ok = all(deps[name] == "ok" for name in CRITICAL)
        return JSONResponse({"ready": ok, "dependencies": deps}, status_code=200 if ok else 503)

    return app


app = create_app()
