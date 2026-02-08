"""
Tests for the in-app notification system.

Tests cover:
- Notification model creation via service
- Notifications REST API (list, unread count, mark read, mark all read)
- Notifications created alongside push notifications in pairings flow
- Auth guards (unauthenticated access denied)
"""
import json
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.player import Player
from app.models.tournament import Tournament
from app.models.pairing import Pairing, GameResult
from app.models.notification import Notification
from app.services.notification import create_notification
from tests.conftest import get_auth_header


# ============================================================================
# Service-level tests
# ============================================================================


class TestNotificationService:
    """Tests for the create_notification helper."""

    @pytest.mark.asyncio
    async def test_create_notification(self, db_session: AsyncSession, test_player: Player):
        """Test creating a notification via the service."""
        notif = await create_notification(
            db_session,
            player_id=test_player.id,
            type="pairing",
            title="Round 1 Pairing",
            body="You play as White vs opponent",
            data={"tournament_id": "t1", "pairing_id": "p1", "opponent_phone": "+254700000002"},
        )
        await db_session.commit()
        await db_session.refresh(notif)

        assert notif.id is not None
        assert notif.player_id == test_player.id
        assert notif.type == "pairing"
        assert notif.title == "Round 1 Pairing"
        assert notif.is_read is False

        parsed = json.loads(notif.data)
        assert parsed["tournament_id"] == "t1"
        assert parsed["opponent_phone"] == "+254700000002"

    @pytest.mark.asyncio
    async def test_create_notification_default_data(self, db_session: AsyncSession, test_player: Player):
        """Test that data defaults to empty JSON object."""
        notif = await create_notification(
            db_session,
            player_id=test_player.id,
            type="result",
            title="Result Confirmed",
            body="Your result was confirmed.",
        )
        await db_session.commit()
        await db_session.refresh(notif)

        assert json.loads(notif.data) == {}


# ============================================================================
# API endpoint tests
# ============================================================================


class TestNotificationsAPI:
    """Tests for the /api/notifications endpoints."""

    @pytest.mark.asyncio
    async def test_list_empty(self, client: AsyncClient, test_player: Player):
        """Test listing when there are no notifications."""
        response = await client.get(
            "/api/notifications",
            headers=get_auth_header(test_player),
        )
        assert response.status_code == 200
        assert response.json() == []

    @pytest.mark.asyncio
    async def test_list_notifications(
        self, client: AsyncClient, db_session: AsyncSession, test_player: Player
    ):
        """Test listing notifications returns them in desc order."""
        await create_notification(db_session, test_player.id, "pairing", "First", "body1")
        await create_notification(db_session, test_player.id, "result", "Second", "body2")
        await db_session.commit()

        response = await client.get(
            "/api/notifications",
            headers=get_auth_header(test_player),
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # Most recent first
        assert data[0]["title"] == "Second"
        assert data[1]["title"] == "First"

    @pytest.mark.asyncio
    async def test_list_unread_only(
        self, client: AsyncClient, db_session: AsyncSession, test_player: Player
    ):
        """Test filtering to unread-only notifications."""
        n1 = await create_notification(db_session, test_player.id, "pairing", "Unread", "body")
        n2 = await create_notification(db_session, test_player.id, "result", "Read", "body")
        await db_session.commit()

        # Mark n2 as read
        n2.is_read = True
        await db_session.commit()

        response = await client.get(
            "/api/notifications?unread_only=true",
            headers=get_auth_header(test_player),
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["title"] == "Unread"

    @pytest.mark.asyncio
    async def test_list_pagination(
        self, client: AsyncClient, db_session: AsyncSession, test_player: Player
    ):
        """Test skip/limit pagination."""
        for i in range(5):
            await create_notification(db_session, test_player.id, "pairing", f"N{i}", "body")
        await db_session.commit()

        response = await client.get(
            "/api/notifications?skip=2&limit=2",
            headers=get_auth_header(test_player),
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    @pytest.mark.asyncio
    async def test_unread_count_zero(self, client: AsyncClient, test_player: Player):
        """Test unread count when no notifications exist."""
        response = await client.get(
            "/api/notifications/unread-count",
            headers=get_auth_header(test_player),
        )
        assert response.status_code == 200
        assert response.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_unread_count(
        self, client: AsyncClient, db_session: AsyncSession, test_player: Player
    ):
        """Test unread count returns correct number."""
        await create_notification(db_session, test_player.id, "pairing", "A", "body")
        await create_notification(db_session, test_player.id, "result", "B", "body")
        n3 = await create_notification(db_session, test_player.id, "claim", "C", "body")
        await db_session.commit()

        # Mark one as read
        n3.is_read = True
        await db_session.commit()

        response = await client.get(
            "/api/notifications/unread-count",
            headers=get_auth_header(test_player),
        )
        assert response.status_code == 200
        assert response.json()["count"] == 2

    @pytest.mark.asyncio
    async def test_mark_read(
        self, client: AsyncClient, db_session: AsyncSession, test_player: Player
    ):
        """Test marking a single notification as read."""
        notif = await create_notification(db_session, test_player.id, "pairing", "Test", "body")
        await db_session.commit()
        await db_session.refresh(notif)

        response = await client.patch(
            f"/api/notifications/{notif.id}/read",
            headers=get_auth_header(test_player),
        )
        assert response.status_code == 200
        assert response.json()["is_read"] is True

        # Verify unread count dropped
        count_resp = await client.get(
            "/api/notifications/unread-count",
            headers=get_auth_header(test_player),
        )
        assert count_resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_mark_read_not_found(self, client: AsyncClient, test_player: Player):
        """Test marking a nonexistent notification as read."""
        response = await client.patch(
            "/api/notifications/nonexistent-id/read",
            headers=get_auth_header(test_player),
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_mark_read_other_player(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_player: Player,
        test_player2: Player,
    ):
        """Test that a player cannot mark another player's notification as read."""
        notif = await create_notification(db_session, test_player.id, "pairing", "Private", "body")
        await db_session.commit()
        await db_session.refresh(notif)

        response = await client.patch(
            f"/api/notifications/{notif.id}/read",
            headers=get_auth_header(test_player2),
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_mark_all_read(
        self, client: AsyncClient, db_session: AsyncSession, test_player: Player
    ):
        """Test marking all notifications as read."""
        await create_notification(db_session, test_player.id, "pairing", "A", "body")
        await create_notification(db_session, test_player.id, "result", "B", "body")
        await create_notification(db_session, test_player.id, "claim", "C", "body")
        await db_session.commit()

        response = await client.patch(
            "/api/notifications/read-all",
            headers=get_auth_header(test_player),
        )
        assert response.status_code == 200

        # Verify all are read
        count_resp = await client.get(
            "/api/notifications/unread-count",
            headers=get_auth_header(test_player),
        )
        assert count_resp.json()["count"] == 0

    @pytest.mark.asyncio
    async def test_isolation_between_players(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        test_player: Player,
        test_player2: Player,
    ):
        """Test that players only see their own notifications."""
        await create_notification(db_session, test_player.id, "pairing", "For P1", "body")
        await create_notification(db_session, test_player2.id, "result", "For P2", "body")
        await db_session.commit()

        resp1 = await client.get(
            "/api/notifications",
            headers=get_auth_header(test_player),
        )
        resp2 = await client.get(
            "/api/notifications",
            headers=get_auth_header(test_player2),
        )

        assert len(resp1.json()) == 1
        assert resp1.json()[0]["title"] == "For P1"
        assert len(resp2.json()) == 1
        assert resp2.json()[0]["title"] == "For P2"


# ============================================================================
# Auth guard tests
# ============================================================================


class TestNotificationsAuthGuard:
    """Test that all endpoints require authentication."""

    @pytest.mark.asyncio
    async def test_list_requires_auth(self, client: AsyncClient):
        response = await client.get("/api/notifications")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_unread_count_requires_auth(self, client: AsyncClient):
        response = await client.get("/api/notifications/unread-count")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_mark_read_requires_auth(self, client: AsyncClient):
        response = await client.patch("/api/notifications/some-id/read")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_mark_all_read_requires_auth(self, client: AsyncClient):
        response = await client.patch("/api/notifications/read-all")
        assert response.status_code == 401


# ============================================================================
# Integration: notifications created during pairing flows
# ============================================================================


class TestNotificationsInPairingFlows:
    """Test that in-app notifications are created by pairing endpoints."""

    @pytest.mark.asyncio
    async def test_claim_result_creates_notification(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        pending_pairing: Pairing,
        test_player: Player,
        test_player2: Player,
        inperson_tournament: Tournament,
    ):
        """Test that claiming a result creates in-app notification for opponent."""
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )
        assert response.status_code == 200

        # Check that opponent got an in-app notification
        result = await db_session.execute(
            select(Notification).where(Notification.player_id == test_player2.id)
        )
        notifications = result.scalars().all()
        assert len(notifications) == 1
        assert notifications[0].type == "claim"
        assert "white_wins" in notifications[0].body
        assert test_player.chess_com_username in notifications[0].body

    @pytest.mark.asyncio
    async def test_confirm_result_creates_notification(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        pending_pairing: Pairing,
        test_player: Player,
        test_player2: Player,
        inperson_tournament: Tournament,
    ):
        """Test that confirming a result creates in-app notification for claimer."""
        # Claim
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )
        # Confirm
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/confirm",
            headers=get_auth_header(test_player2),
        )
        assert response.status_code == 200

        # Claimer (test_player) should have a "confirm" notification
        result = await db_session.execute(
            select(Notification).where(
                Notification.player_id == test_player.id,
                Notification.type == "confirm",
            )
        )
        notifications = result.scalars().all()
        assert len(notifications) == 1
        assert "confirmed" in notifications[0].body.lower()

    @pytest.mark.asyncio
    async def test_dispute_result_creates_notification(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        pending_pairing: Pairing,
        test_player: Player,
        test_player2: Player,
        inperson_tournament: Tournament,
    ):
        """Test that disputing a result creates in-app notification for claimer."""
        # Claim
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )
        # Dispute
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/dispute",
            json={"reason": "Wrong result"},
            headers=get_auth_header(test_player2),
        )
        assert response.status_code == 200

        # Claimer (test_player) should have a "dispute" notification
        result = await db_session.execute(
            select(Notification).where(
                Notification.player_id == test_player.id,
                Notification.type == "dispute",
            )
        )
        notifications = result.scalars().all()
        assert len(notifications) == 1
        assert "disputed" in notifications[0].body.lower()

        # Check data includes reason
        data = json.loads(notifications[0].data)
        assert data["reason"] == "Wrong result"

    @pytest.mark.asyncio
    async def test_claim_no_show_creates_notification(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        pending_pairing: Pairing,
        test_player: Player,
        test_player2: Player,
        inperson_tournament: Tournament,
    ):
        """Test that claiming no-show creates in-app notification for accused."""
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-no-show",
            json={"reason": "Opponent didn't show"},
            headers=get_auth_header(test_player),
        )
        assert response.status_code == 200

        # Accused player (test_player2) should have a "no_show" notification
        result = await db_session.execute(
            select(Notification).where(
                Notification.player_id == test_player2.id,
                Notification.type == "no_show",
            )
        )
        notifications = result.scalars().all()
        assert len(notifications) == 1
        assert "no-show" in notifications[0].title.lower() or "no_show" in notifications[0].type
