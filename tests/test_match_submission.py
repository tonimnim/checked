"""
Tests for match/result submission functionality.

Tests cover:
- In-person tournaments: claim, confirm, dispute, cancel claim
- Online tournaments: submit Chess.com game URL
- Edge cases and error handling
"""
import pytest
from datetime import datetime, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.player import Player
from app.models.tournament import Tournament
from app.models.pairing import Pairing, GameResult
from tests.conftest import get_auth_header


class TestInPersonResultClaim:
    """Tests for in-person tournament result claiming."""

    @pytest.mark.asyncio
    async def test_claim_result_white_wins(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        inperson_tournament: Tournament,
    ):
        """Test claiming white wins result."""
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["claimed_result"] == "white_wins"
        assert data["claimed_by"] == test_player.id
        assert data["has_pending_claim"] is True
        assert data["result"] == "pending"  # Not confirmed yet

    @pytest.mark.asyncio
    async def test_claim_result_black_wins(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player2: Player,
        inperson_tournament: Tournament,
    ):
        """Test black player claiming black wins."""
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "black_wins"},
            headers=get_auth_header(test_player2),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["claimed_result"] == "black_wins"
        assert data["claimed_by"] == test_player2.id

    @pytest.mark.asyncio
    async def test_claim_result_draw(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        inperson_tournament: Tournament,
    ):
        """Test claiming draw result."""
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "draw"},
            headers=get_auth_header(test_player),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["claimed_result"] == "draw"

    @pytest.mark.asyncio
    async def test_claim_result_not_participant(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        admin_player: Player,
        inperson_tournament: Tournament,
    ):
        """Test that non-participants cannot claim results."""
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(admin_player),
        )

        assert response.status_code == 403
        assert "not a player" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_claim_result_online_tournament_rejected(
        self,
        client: AsyncClient,
        online_pairing: Pairing,
        test_player: Player,
        online_tournament: Tournament,
    ):
        """Test that claim-result is rejected for online tournaments."""
        response = await client.post(
            f"/api/tournaments/{online_tournament.id}/pairings/{online_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )

        assert response.status_code == 400
        assert "online tournament" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_cannot_claim_twice(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        test_player2: Player,
        inperson_tournament: Tournament,
    ):
        """Test that a second claim is rejected when one is pending."""
        # First claim
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )

        # Second claim should fail
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "black_wins"},
            headers=get_auth_header(test_player2),
        )

        assert response.status_code == 400
        assert "pending" in response.json()["detail"].lower()


class TestInPersonResultConfirmation:
    """Tests for confirming claimed results."""

    @pytest.mark.asyncio
    async def test_confirm_result(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        pending_pairing: Pairing,
        test_player: Player,
        test_player2: Player,
        inperson_tournament: Tournament,
    ):
        """Test opponent confirming a claimed result."""
        # First, white claims white wins
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )

        # Black confirms
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/confirm",
            headers=get_auth_header(test_player2),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "white_wins"
        assert data["confirmed_by"] == test_player2.id
        assert data["has_pending_claim"] is False

    @pytest.mark.asyncio
    async def test_claimer_cannot_confirm_own_claim(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        inperson_tournament: Tournament,
    ):
        """Test that claimer cannot confirm their own claim."""
        # Claim result
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )

        # Try to confirm own claim
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/confirm",
            headers=get_auth_header(test_player),
        )

        assert response.status_code == 400
        assert "cannot confirm your own" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_confirm_without_claim(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        inperson_tournament: Tournament,
    ):
        """Test confirming when there's no pending claim."""
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/confirm",
            headers=get_auth_header(test_player),
        )

        assert response.status_code == 400
        assert "no pending" in response.json()["detail"].lower()


class TestInPersonResultDispute:
    """Tests for disputing claimed results."""

    @pytest.mark.asyncio
    async def test_dispute_result(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        test_player2: Player,
        inperson_tournament: Tournament,
    ):
        """Test opponent disputing a claimed result."""
        # White claims white wins
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )

        # Black disputes
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/dispute",
            json={"reason": "I actually won this game"},
            headers=get_auth_header(test_player2),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["is_disputed"] is True
        assert data["dispute_reason"] == "I actually won this game"
        assert data["result"] == "pending"  # Still pending, needs admin

    @pytest.mark.asyncio
    async def test_claimer_cannot_dispute_own_claim(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        inperson_tournament: Tournament,
    ):
        """Test that claimer cannot dispute their own claim."""
        # Claim result
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )

        # Try to dispute own claim
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/dispute",
            json={"reason": "Changed my mind"},
            headers=get_auth_header(test_player),
        )

        assert response.status_code == 400
        assert "cannot dispute your own" in response.json()["detail"].lower()


class TestCancelClaim:
    """Tests for cancelling result claims."""

    @pytest.mark.asyncio
    async def test_cancel_claim_within_window(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        inperson_tournament: Tournament,
    ):
        """Test cancelling claim within 2-minute window."""
        # Claim result
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )

        # Cancel immediately (within window)
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/cancel-claim",
            headers=get_auth_header(test_player),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["claimed_result"] is None
        assert data["claimed_by"] is None
        assert data["has_pending_claim"] is False

    @pytest.mark.asyncio
    async def test_opponent_cannot_cancel_claim(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        test_player2: Player,
        inperson_tournament: Tournament,
    ):
        """Test that opponent cannot cancel someone else's claim."""
        # White claims
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )

        # Black tries to cancel
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/cancel-claim",
            headers=get_auth_header(test_player2),
        )

        assert response.status_code == 403
        assert "only the claimer" in response.json()["detail"].lower()


class TestAdminOverride:
    """Tests for admin result override."""

    @pytest.mark.asyncio
    async def test_admin_override_disputed_result(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        test_player2: Player,
        admin_player: Player,
        inperson_tournament: Tournament,
    ):
        """Test admin resolving a disputed result."""
        # Create disputed claim
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/dispute",
            json={"reason": "Disagree"},
            headers=get_auth_header(test_player2),
        )

        # Admin overrides
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/admin-override",
            json={"result": "draw", "reason": "Arbiter decision after review"},
            headers=get_auth_header(admin_player),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "draw"
        assert data["is_disputed"] is False

    @pytest.mark.asyncio
    async def test_non_admin_cannot_override(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        inperson_tournament: Tournament,
    ):
        """Test that non-admin cannot use admin override."""
        response = await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/admin-override",
            json={"result": "draw"},
            headers=get_auth_header(test_player),
        )

        assert response.status_code == 403


class TestMyMatches:
    """Tests for fetching player's matches."""

    @pytest.mark.asyncio
    async def test_get_my_matches(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
    ):
        """Test fetching current player's matches."""
        response = await client.get(
            "/api/matches/my-matches",
            headers=get_auth_header(test_player),
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(m["id"] == pending_pairing.id for m in data)

    @pytest.mark.asyncio
    async def test_get_my_matches_with_tournament_info(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        inperson_tournament: Tournament,
    ):
        """Test that matches include tournament information."""
        response = await client.get(
            "/api/matches/my-matches",
            headers=get_auth_header(test_player),
        )

        assert response.status_code == 200
        data = response.json()
        match = next(m for m in data if m["id"] == pending_pairing.id)

        assert "tournament" in match
        assert match["tournament"]["id"] == inperson_tournament.id
        assert match["tournament"]["name"] == inperson_tournament.name
        assert match["tournament"]["is_online"] is False

    @pytest.mark.asyncio
    async def test_action_required_count(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        test_player2: Player,
        inperson_tournament: Tournament,
    ):
        """Test action required count endpoint."""
        # Create a claim that test_player2 needs to confirm
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )

        # Check count for test_player2 (should need to confirm)
        response = await client.get(
            "/api/matches/action-required/count",
            headers=get_auth_header(test_player2),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        assert data["needs_confirmation"] >= 1


class TestOnlineGameSubmission:
    """Tests for online tournament game URL submission."""

    @pytest.mark.asyncio
    async def test_submit_game_url_validation(
        self,
        client: AsyncClient,
        online_pairing: Pairing,
        test_player: Player,
        online_tournament: Tournament,
    ):
        """Test that invalid game URLs are rejected."""
        response = await client.post(
            f"/api/tournaments/{online_tournament.id}/pairings/{online_pairing.id}/submit-game",
            json={"game_url": "not-a-valid-url"},
            headers=get_auth_header(test_player),
        )

        # Should fail validation or Chess.com API check
        assert response.status_code in [200, 400, 422]
        if response.status_code == 200:
            data = response.json()
            assert data["valid"] is False

    @pytest.mark.asyncio
    async def test_non_participant_cannot_submit(
        self,
        client: AsyncClient,
        online_pairing: Pairing,
        admin_player: Player,
        online_tournament: Tournament,
    ):
        """Test that non-participants cannot submit game URLs."""
        response = await client.post(
            f"/api/tournaments/{online_tournament.id}/pairings/{online_pairing.id}/submit-game",
            json={"game_url": "https://www.chess.com/game/live/12345"},
            headers=get_auth_header(admin_player),
        )

        assert response.status_code == 403


class TestResultScoring:
    """Tests for score updates after result confirmation."""

    @pytest.mark.asyncio
    async def test_white_wins_updates_scores(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
        pending_pairing: Pairing,
        test_player: Player,
        test_player2: Player,
        inperson_tournament: Tournament,
    ):
        """Test that confirming white wins updates player scores."""
        # Claim and confirm white wins
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "white_wins"},
            headers=get_auth_header(test_player),
        )
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/confirm",
            headers=get_auth_header(test_player2),
        )

        # Check tournament standings
        response = await client.get(
            f"/api/tournaments/{inperson_tournament.id}/standings",
        )

        assert response.status_code == 200
        standings = response.json()

        # Find players in standings
        white_standing = next((s for s in standings if s["player_id"] == test_player.id), None)
        black_standing = next((s for s in standings if s["player_id"] == test_player2.id), None)

        if white_standing and black_standing:
            assert white_standing["score"] == 1.0
            assert white_standing["wins"] == 1
            assert black_standing["score"] == 0.0
            assert black_standing["losses"] == 1

    @pytest.mark.asyncio
    async def test_draw_updates_scores(
        self,
        client: AsyncClient,
        pending_pairing: Pairing,
        test_player: Player,
        test_player2: Player,
        inperson_tournament: Tournament,
    ):
        """Test that confirming draw gives both players 0.5 points."""
        # Claim and confirm draw
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/claim-result",
            json={"result": "draw"},
            headers=get_auth_header(test_player),
        )
        await client.post(
            f"/api/tournaments/{inperson_tournament.id}/pairings/{pending_pairing.id}/confirm",
            headers=get_auth_header(test_player2),
        )

        # Check standings
        response = await client.get(
            f"/api/tournaments/{inperson_tournament.id}/standings",
        )

        assert response.status_code == 200
        standings = response.json()

        for standing in standings:
            if standing["player_id"] in [test_player.id, test_player2.id]:
                assert standing["score"] == 0.5
                assert standing["draws"] == 1
