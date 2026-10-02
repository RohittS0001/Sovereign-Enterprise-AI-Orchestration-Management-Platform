
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from backend.app.api.agent import router as agent_router
from backend.app.api.health import router as health_router
from backend.app.core.config.settings import settings
from backend.app.core.exceptions.exceptions import AppException
from backend.app.core.logging.logger import get_logger


logger = get_logger(__name__)


app = FastAPI(
    title=settings.app_name,
    version=settings.version,
)

app.include_router(health_router)
app.include_router(agent_router)

@app.exception_handler(AppException)
async def app_exception_handler(
    request: Request,
    exc: AppException,
) -> JSONResponse:
    logger.error(
        "Application error: %s | Path: %s",
        exc.message,
        request.url.path,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.message,
            "status_code": exc.status_code,
        },
    )
