import logging
import re
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.config import settings

logger = logging.getLogger(__name__)

from app.api import (
    auth_router,
    products_router,
    clients_router,
    sales_router,
    dashboard_router,
    settings_router,
    brands_router,
    colors_router,
    seasons_router,
    finance_router,
    sizes_router,
    product_variants_router,
    marketing_router,
    reports_router,
)


app = FastAPI(
    title="Enrico Cerrini Backend API",
    description="Backend API for Enrico Cerrini clothing store management system",
    version="1.0.0",
)

# Add CORS middleware with cookie support for local and LAN development
allowed_origins = [origin.strip() for origin in settings.cors_origin.split(",") if origin.strip()]

# Allow any LAN IP like http://192.168.x.x:3000 or http://10.x.x.x:3000 (and other ports)
ALLOWED_ORIGIN_REGEX = (
    r"^http://(localhost|127\.0\.0\.1|(10|172\.(1[6-9]|2[0-9]|3[0-1])|192\.168)"
    r"(?:\.\d{1,3}){1,2})(?::\d+)?$"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=ALLOWED_ORIGIN_REGEX,
    allow_credentials=True,  # Required for cookies
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Set-Cookie"],  # Expose Set-Cookie header
)


def _cors_headers(request) -> dict:
    """
    CORS headers for responses produced by exception handlers.

    Starlette runs the `Exception` handler in ServerErrorMiddleware, which sits
    *outside* CORSMiddleware, so 500 responses never pass through it. Without
    these headers the browser blocks the body and the frontend only sees an
    opaque network error instead of the real message.
    """
    origin = request.headers.get("origin")
    if not origin:
        return {}

    if origin in allowed_origins or re.match(ALLOWED_ORIGIN_REGEX, origin):
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    # Log the full traceback — otherwise the cause of a 500 is invisible in prod.
    logger.exception(
        "Unhandled error on %s %s", request.method, request.url.path, exc_info=exc
    )
    # The exception text is deliberately not returned: it carries SQL fragments,
    # constraint and column names and file paths. It is in the log above instead.
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "message": "Internal server error",
            "detail": "Internal server error",
            "errors": [],
        },
        headers=_cors_headers(request),
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    return JSONResponse(
        status_code=exc.status_code,
        # `detail` is kept alongside `message` because the frontend reads both.
        content={
            "success": False,
            "message": exc.detail,
            "detail": exc.detail,
            "errors": [],
        },
        headers=_cors_headers(request),
    )


# Include routers
app.include_router(auth_router)
app.include_router(products_router)
app.include_router(clients_router)
app.include_router(sales_router)
app.include_router(dashboard_router)
app.include_router(settings_router)
app.include_router(brands_router)
app.include_router(colors_router)
app.include_router(seasons_router)
app.include_router(finance_router)
app.include_router(sizes_router)
app.include_router(product_variants_router)
app.include_router(marketing_router)
app.include_router(reports_router)



@app.on_event("startup")
def on_startup():
    from app.utils.init_db import create_initial_admin
    create_initial_admin()


# Health check endpoint
@app.get("/health")
async def health_check():
    return {
        "success": True,
        "message": "API is running",
        "data": {"status": "healthy", "version": "1.0.0"},
    }


# Root endpoint
@app.get("/")
async def root():
    return {
        "success": True,
        "message": "Enrico Cerrini Backend API",
        "data": {
            "title": "Enrico Cerrini Backend API",
            "version": "1.0.0",
            "docs": "/docs",
        },
    }


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.server.debug,
    )
