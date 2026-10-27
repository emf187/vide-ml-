"""
FastAPI application factory.

Creates and configures the app with all routes, middleware,
and lifecycle hooks.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api.middleware import RequestLoggingMiddleware
from api.routes import health, machines, readings, anomalies, maintenance, diagnostics, configuration, ml
from config.settings import get_settings
from data_acquisition.manager import AcquisitionManager
from services.reading_service import ReadingService
from storage.database import init_db
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)

# Global acquisition manager — accessible to health endpoint
_acquisition_manager: Optional[AcquisitionManager] = None


def _get_or_create_acquisition_manager() -> AcquisitionManager:
    global _acquisition_manager
    if _acquisition_manager is None:
        reading_svc = ReadingService()
        _acquisition_manager = AcquisitionManager(on_reading=reading_svc.process)
    return _acquisition_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    setup_logging()
    logger.info("Vide Industrial starting up...")

    # Initialize database
    init_db()
    logger.info("Database ready")

    # Start data acquisition
    mgr = _get_or_create_acquisition_manager()
    mgr.start()
    logger.info("Data acquisition started")

    yield

    # Shutdown
    if _acquisition_manager:
        _acquisition_manager.stop()
    logger.info("Vide Industrial shut down cleanly")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = get_settings()

    app = FastAPI(
        title="Vide Industrial",
        description="Production-ready industrial fault detection and predictive maintenance platform.",
        version="3.0.0",
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.api.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request logging
    app.add_middleware(RequestLoggingMiddleware)

    # Mount static files (if directory exists)
    import os
    if os.path.isdir("static"):
        app.mount("/static", StaticFiles(directory="static"), name="static")

    # Register routes
    app.include_router(health.router)
    app.include_router(machines.router)
    app.include_router(readings.router)
    app.include_router(anomalies.router)
    app.include_router(maintenance.router)
    app.include_router(diagnostics.router)
    app.include_router(configuration.router)
    app.include_router(ml.router)

    # Expose acquisition manager for health endpoint
    @app.get("/api/health/detailed")
    def detailed_health():
        from api.routes.health import health_check
        result = health_check()
        mgr = _get_or_create_acquisition_manager()
        result["acquisition"] = mgr.get_status()
        return result

    return app