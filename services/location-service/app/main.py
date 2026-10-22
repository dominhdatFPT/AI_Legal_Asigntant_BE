"""Location Service application and route registration."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from common_auth.fastapi_dep import AuthHTTPException, add_auth_error_handler

from app.api.administrative import router as administrative_router
from app.api.offices import router as offices_router
from app.core.config import LocationSettings
from app.core.errors import failure, install_error_handlers
from app.services.goong_client import GoongClient
from app.services.search_limits import SearchLimiter


@asynccontextmanager
async def lifespan(app):
    settings = LocationSettings.from_env()
    app.state.location_settings = settings
    app.state.search_limiter = SearchLimiter()
    app.state.goong_client = GoongClient(settings.goong_api_key,
                                         timeout=settings.goong_timeout_seconds,
                                         max_rps=settings.goong_max_rps,
                                         daily_budget=settings.goong_daily_budget)
    try:
        yield
    finally:
        await app.state.goong_client.aclose()


app = FastAPI(title="Location Service", version="0.1.0", lifespan=lifespan)
install_error_handlers(app)
add_auth_error_handler(app)


@app.exception_handler(AuthHTTPException)
async def location_auth_error(request, exc):
    # Keep common_auth's token validation, but use Location's sanitized request_id.
    return failure(request, exc.status_code, exc.detail["code"], exc.detail["message"],
                   headers=exc.headers)


@app.get("/api/v1/locations/health")
def health():
    return {"status": "ok", "service": "location-service"}

app.include_router(administrative_router)
app.include_router(offices_router)
