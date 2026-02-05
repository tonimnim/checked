"""
WebSocket Connection Manager for real-time updates

Handles:
- Player connections with JWT authentication
- Tournament room subscriptions
- Broadcasting events to relevant players
"""
from typing import Dict, Set, Optional, Any
from fastapi import WebSocket, WebSocketDisconnect
from datetime import datetime
import json

from app.services.auth import AuthService


class ConnectionManager:
    """
    Manages WebSocket connections and room subscriptions.

    Structure:
    - connections: {player_id: WebSocket}
    - player_tournaments: {player_id: Set[tournament_ids]}
    - tournament_players: {tournament_id: Set[player_ids]}
    """

    def __init__(self):
        # Active connections by player
        self.connections: Dict[str, WebSocket] = {}

        # Which tournaments each player is subscribed to
        self.player_tournaments: Dict[str, Set[str]] = {}

        # Which players are in each tournament room
        self.tournament_players: Dict[str, Set[str]] = {}

    async def connect(self, websocket: WebSocket, player_id: str):
        """Accept connection and register player"""
        await websocket.accept()
        self.connections[player_id] = websocket
        self.player_tournaments[player_id] = set()
        print(f"[WS] Player {player_id} connected")

    def disconnect(self, player_id: str):
        """Remove player from all rooms and connections"""
        if player_id in self.connections:
            del self.connections[player_id]

        # Remove from all tournament rooms
        if player_id in self.player_tournaments:
            for tournament_id in self.player_tournaments[player_id]:
                if tournament_id in self.tournament_players:
                    self.tournament_players[tournament_id].discard(player_id)
            del self.player_tournaments[player_id]

        print(f"[WS] Player {player_id} disconnected")

    def subscribe_to_tournament(self, player_id: str, tournament_id: str):
        """Subscribe player to tournament room"""
        if player_id not in self.player_tournaments:
            self.player_tournaments[player_id] = set()

        self.player_tournaments[player_id].add(tournament_id)

        if tournament_id not in self.tournament_players:
            self.tournament_players[tournament_id] = set()

        self.tournament_players[tournament_id].add(player_id)
        print(f"[WS] Player {player_id} subscribed to tournament {tournament_id}")

    def unsubscribe_from_tournament(self, player_id: str, tournament_id: str):
        """Unsubscribe player from tournament room"""
        if player_id in self.player_tournaments:
            self.player_tournaments[player_id].discard(tournament_id)

        if tournament_id in self.tournament_players:
            self.tournament_players[tournament_id].discard(player_id)

    async def send_to_player(self, player_id: str, message: dict):
        """Send message to specific player"""
        if player_id in self.connections:
            try:
                await self.connections[player_id].send_json(message)
            except Exception as e:
                print(f"[WS] Error sending to {player_id}: {e}")
                self.disconnect(player_id)

    async def broadcast_to_tournament(self, tournament_id: str, message: dict, exclude: Optional[Set[str]] = None):
        """Broadcast message to all players in a tournament"""
        if tournament_id not in self.tournament_players:
            return

        exclude = exclude or set()
        disconnected = []

        for player_id in self.tournament_players[tournament_id]:
            if player_id in exclude:
                continue

            if player_id in self.connections:
                try:
                    await self.connections[player_id].send_json(message)
                except Exception:
                    disconnected.append(player_id)

        # Clean up disconnected
        for player_id in disconnected:
            self.disconnect(player_id)

    async def broadcast_to_players(self, player_ids: list, message: dict):
        """Broadcast message to specific players"""
        for player_id in player_ids:
            await self.send_to_player(player_id, message)

    def get_stats(self) -> dict:
        """Get connection statistics"""
        return {
            "total_connections": len(self.connections),
            "tournament_rooms": len(self.tournament_players),
            "connections_per_room": {
                tid: len(players) for tid, players in self.tournament_players.items()
            }
        }


# Singleton instance
ws_manager = ConnectionManager()


# Event builders
def build_event(event_type: str, tournament_id: str, data: Any) -> dict:
    """Build standardized event message"""
    return {
        "event": event_type,
        "tournament_id": tournament_id,
        "data": data,
        "timestamp": datetime.utcnow().isoformat()
    }


async def notify_pairing_created(
    tournament_id: str,
    white_player_id: str,
    black_player_id: str,
    pairing_data: dict
):
    """Notify both players that they've been paired"""
    # Notify white player
    await ws_manager.send_to_player(white_player_id, build_event(
        "pairing_created",
        tournament_id,
        {
            **pairing_data,
            "you_play_as": "white"
        }
    ))

    # Notify black player
    await ws_manager.send_to_player(black_player_id, build_event(
        "pairing_created",
        tournament_id,
        {
            **pairing_data,
            "you_play_as": "black"
        }
    ))


async def notify_result_submitted(
    tournament_id: str,
    pairing_id: str,
    white_player_id: str,
    black_player_id: str,
    result: str
):
    """Notify both players that result was submitted"""
    message = build_event(
        "result_submitted",
        tournament_id,
        {
            "pairing_id": pairing_id,
            "result": result
        }
    )
    await ws_manager.broadcast_to_players([white_player_id, black_player_id], message)


async def notify_no_show_claimed(
    tournament_id: str,
    pairing_id: str,
    claimed_by: str,
    accused_player_id: str
):
    """Notify accused player that no-show was claimed against them"""
    await ws_manager.send_to_player(accused_player_id, build_event(
        "no_show_claimed",
        tournament_id,
        {
            "pairing_id": pairing_id,
            "message": "Your opponent claims you didn't show up. Submit the game URL to dispute."
        }
    ))


async def notify_standings_updated(tournament_id: str):
    """Notify all players in tournament that standings changed"""
    await ws_manager.broadcast_to_tournament(tournament_id, build_event(
        "standings_updated",
        tournament_id,
        {"message": "Standings have been updated"}
    ))


async def notify_round_started(tournament_id: str, round_number: int):
    """Notify all players that a new round has started"""
    await ws_manager.broadcast_to_tournament(tournament_id, build_event(
        "round_started",
        tournament_id,
        {
            "round": round_number,
            "message": f"Round {round_number} pairings are ready"
        }
    ))


# In-person tournament result claim/confirmation events

async def notify_result_claimed(
    tournament_id: str,
    pairing_id: str,
    claimer_id: str,
    opponent_id: str,
    claimed_result: str,
    confirmation_deadline: str
):
    """Notify opponent that result was claimed - needs confirmation"""
    await ws_manager.send_to_player(opponent_id, build_event(
        "result_claimed",
        tournament_id,
        {
            "pairing_id": pairing_id,
            "claimed_result": claimed_result,
            "confirmation_deadline": confirmation_deadline,
            "message": "Your opponent has claimed a result. Please confirm or dispute."
        }
    ))


async def notify_result_confirmed(
    tournament_id: str,
    pairing_id: str,
    claimer_id: str,
    confirmer_id: str,
    final_result: str
):
    """Notify claimer that result was confirmed"""
    # Notify the claimer
    await ws_manager.send_to_player(claimer_id, build_event(
        "result_confirmed",
        tournament_id,
        {
            "pairing_id": pairing_id,
            "result": final_result,
            "message": "Your result claim has been confirmed."
        }
    ))
    # Also broadcast to tournament for standings update
    await ws_manager.broadcast_to_tournament(tournament_id, build_event(
        "standings_updated",
        tournament_id,
        {"message": "Standings have been updated"}
    ))


async def notify_result_dispute(
    tournament_id: str,
    pairing_id: str,
    claimer_id: str,
    disputer_id: str,
    reason: str
):
    """Notify claimer that result was disputed"""
    await ws_manager.send_to_player(claimer_id, build_event(
        "result_disputed",
        tournament_id,
        {
            "pairing_id": pairing_id,
            "reason": reason,
            "message": "Your result claim has been disputed. An arbiter will review."
        }
    ))


async def notify_claim_cancelled(
    tournament_id: str,
    pairing_id: str,
    opponent_id: str
):
    """Notify opponent that the result claim was cancelled"""
    await ws_manager.send_to_player(opponent_id, build_event(
        "claim_cancelled",
        tournament_id,
        {
            "pairing_id": pairing_id,
            "message": "The result claim has been cancelled."
        }
    ))
