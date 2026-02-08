from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db, get_db_stats, optimize_db
from app.routers import (
    auth_router, players_router, tournaments_router,
    pairings_router, utils_router, websocket_router
)
from app.routers.pairings import matches_router
from app.routers.analytics import router as analytics_router, record_request
from app.routers.security import router as security_router
from app.routers.clubs import router as clubs_router
from app.services.background_sync import start_background_sync, stop_background_sync
from app.services.tournament_automation import start_tournament_automation, stop_tournament_automation

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create database tables
    await init_db()
    # Start background Chess.com sync (every 30 minutes)
    start_background_sync()
    # Start tournament automation (every 5 minutes)
    start_tournament_automation()
    yield
    # Shutdown: Stop background tasks
    stop_background_sync()
    stop_tournament_automation()


app = FastAPI(
    title=settings.app_name,
    description="Kenyan Chess Tournament Management System - Powered by Chess.com",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS middleware - production-aware
CORS_ORIGINS = (
    ["*"] if not settings.is_production() else [
        "https://chesskenya.com",
        "https://www.chesskenya.com",
        "https://checked.co.ke",
        "https://www.checked.co.ke",
        "https://checked-kappa.vercel.app",
    ]
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request tracking middleware for analytics
@app.middleware("http")
async def track_requests(request, call_next):
    """Track API requests for analytics dashboard"""
    record_request()
    response = await call_next(request)
    return response

# Include routers
app.include_router(auth_router)
app.include_router(players_router)
app.include_router(tournaments_router)
app.include_router(pairings_router)
app.include_router(utils_router)
app.include_router(websocket_router)
app.include_router(analytics_router)
app.include_router(security_router)
app.include_router(clubs_router)
app.include_router(matches_router)


@app.get("/")
async def root():
    return {
        "name": settings.app_name,
        "description": "Kenyan Chess Tournament Management System",
        "version": "1.0.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/stats")
async def db_stats():
    """Database statistics for monitoring"""
    stats = await get_db_stats()
    if stats.get("db_size_bytes"):
        stats["db_size_mb"] = round(stats["db_size_bytes"] / (1024 * 1024), 2)
    return stats


@app.post("/admin/optimize")
async def run_optimization():
    """
    Run database optimization (ANALYZE + WAL checkpoint).
    Call this periodically (e.g., daily via cron).
    """
    await optimize_db()
    return {"status": "optimized"}
