"""
WebSocket endpoint for real-time updates
"""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select
from typing import Optional

from app.database import async_session_maker
from app.models.player import Player
from app.models.tournament import TournamentPlayer
from app.services.auth import AuthService
from app.services.websocket import ws_manager

router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: Optional[str] = Query(None)
):
    """
    WebSocket connection endpoint.

    Connect with: ws://localhost:8000/ws?token=YOUR_JWT_TOKEN

    Messages from client:
    - {"action": "subscribe", "tournament_id": "xxx"} - Subscribe to tournament updates
    - {"action": "unsubscribe", "tournament_id": "xxx"} - Unsubscribe
    - {"action": "ping"} - Keep alive

    Messages from server:
    - {"event": "pairing_created", "tournament_id": "xxx", "data": {...}}
    - {"event": "result_submitted", "tournament_id": "xxx", "data": {...}}
    - {"event": "no_show_claimed", "tournament_id": "xxx", "data": {...}}
    - {"event": "standings_updated", "tournament_id": "xxx", "data": {...}}
    - {"event": "round_started", "tournament_id": "xxx", "data": {...}}
    """
    # Validate token
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return

    token_data = AuthService.decode_token(token)
    if not token_data or not token_data.player_id:
        await websocket.close(code=4002, reason="Invalid token")
        return

    player_id = token_data.player_id

    # Verify player exists
    async with async_session_maker() as db:
        result = await db.execute(
            select(Player).where(Player.id == player_id, Player.is_active == True)
        )
        player = result.scalar_one_or_none()

        if not player:
            await websocket.close(code=4003, reason="Player not found")
            return

        # Accept connection
        await ws_manager.connect(websocket, player_id)

        # Auto-subscribe to player's active tournaments
        result = await db.execute(
            select(TournamentPlayer.tournament_id).where(
                TournamentPlayer.player_id == player_id,
                TournamentPlayer.is_withdrawn == False
            )
        )
        tournament_ids = result.scalars().all()

        for tid in tournament_ids:
            ws_manager.subscribe_to_tournament(player_id, tid)

    try:
        # Send welcome message
        await websocket.send_json({
            "event": "connected",
            "data": {
                "player_id": player_id,
                "subscribed_tournaments": list(ws_manager.player_tournaments.get(player_id, set()))
            }
        })

        # Listen for messages
        while True:
            data = await websocket.receive_json()
            action = data.get("action")

            if action == "ping":
                await websocket.send_json({"event": "pong"})

            elif action == "subscribe":
                tournament_id = data.get("tournament_id")
                if tournament_id:
                    # Verify player is in this tournament
                    async with async_session_maker() as db:
                        result = await db.execute(
                            select(TournamentPlayer).where(
                                TournamentPlayer.tournament_id == tournament_id,
                                TournamentPlayer.player_id == player_id,
                                TournamentPlayer.is_withdrawn == False
                            )
                        )
                        if result.scalar_one_or_none():
                            ws_manager.subscribe_to_tournament(player_id, tournament_id)
                            await websocket.send_json({
                                "event": "subscribed",
                                "data": {"tournament_id": tournament_id}
                            })
                        else:
                            await websocket.send_json({
                                "event": "error",
                                "data": {"message": "You are not in this tournament"}
                            })

            elif action == "unsubscribe":
                tournament_id = data.get("tournament_id")
                if tournament_id:
                    ws_manager.unsubscribe_from_tournament(player_id, tournament_id)
                    await websocket.send_json({
                        "event": "unsubscribed",
                        "data": {"tournament_id": tournament_id}
                    })

            elif action == "status":
                # Return connection status
                await websocket.send_json({
                    "event": "status",
                    "data": {
                        "subscribed_tournaments": list(ws_manager.player_tournaments.get(player_id, set()))
                    }
                })

    except WebSocketDisconnect:
        ws_manager.disconnect(player_id)
    except Exception as e:
        print(f"[WS] Error for player {player_id}: {e}")
        ws_manager.disconnect(player_id)


@router.get("/ws/stats")
async def websocket_stats():
    """Get WebSocket connection statistics (for monitoring)"""
    return ws_manager.get_stats()
