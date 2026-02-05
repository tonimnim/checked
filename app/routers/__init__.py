from app.routers.auth import router as auth_router
from app.routers.players import router as players_router
from app.routers.tournaments import router as tournaments_router
from app.routers.pairings import router as pairings_router
from app.routers.utils import router as utils_router
from app.routers.websocket import router as websocket_router

__all__ = [
    "auth_router", "players_router", "tournaments_router",
    "pairings_router", "utils_router", "websocket_router"
]
