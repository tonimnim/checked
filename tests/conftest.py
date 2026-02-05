"""
Pytest configuration and fixtures for ChessKenya tests.
"""
import asyncio
import os
import uuid
from datetime import datetime, timedelta
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

# Set test environment BEFORE importing app modules
os.environ["TESTING"] = "1"

# Clear any cached settings to ensure test config is used
from app.config import get_settings
get_settings.cache_clear()

from app.database import Base, get_db, engine, async_session_maker
from app.main import app
from app.models.player import Player
from app.models.tournament import Tournament, TournamentPlayer, TournamentStatus, TournamentFormat
from app.models.pairing import Pairing, GameResult
from app.services.auth import AuthService


@pytest.fixture(scope="session")
def event_loop() -> Generator:
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a fresh database session for each test with transaction rollback."""
    # Create all tables at the start
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    # Create a connection and begin a transaction
    async with engine.connect() as conn:
        # Begin a transaction that we'll rollback at the end
        trans = await conn.begin()

        # Create a session bound to this connection
        session = AsyncSession(bind=conn, expire_on_commit=False)

        try:
            yield session
        finally:
            await session.close()
            # Rollback the transaction to clean up
            await trans.rollback()


@pytest_asyncio.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create test client with database session override."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def test_player(db_session: AsyncSession) -> Player:
    """Create a test player."""
    player = Player(
        id=str(uuid.uuid4()),
        chess_com_username="test_player",
        phone="+254700000001",
        password_hash=AuthService.hash_password("testpass123"),
        county="Nairobi",
        age=25,
        gender="male",
        rating_rapid=1500,
        rating_blitz=1400,
        is_active=True,
    )
    db_session.add(player)
    await db_session.commit()
    await db_session.refresh(player)
    return player


@pytest_asyncio.fixture
async def test_player2(db_session: AsyncSession) -> Player:
    """Create a second test player (opponent)."""
    player = Player(
        id=str(uuid.uuid4()),
        chess_com_username="test_opponent",
        phone="+254700000002",
        password_hash=AuthService.hash_password("testpass123"),
        county="Mombasa",
        age=28,
        gender="male",
        rating_rapid=1600,
        rating_blitz=1500,
        is_active=True,
    )
    db_session.add(player)
    await db_session.commit()
    await db_session.refresh(player)
    return player


@pytest_asyncio.fixture
async def admin_player(db_session: AsyncSession) -> Player:
    """Create an admin player."""
    player = Player(
        id=str(uuid.uuid4()),
        chess_com_username="admin_user",
        phone="+254700000099",
        password_hash=AuthService.hash_password("adminpass123"),
        county="Nairobi",
        age=35,
        gender="male",
        is_admin=True,
        is_active=True,
    )
    db_session.add(player)
    await db_session.commit()
    await db_session.refresh(player)
    return player


@pytest_asyncio.fixture
async def online_tournament(db_session: AsyncSession) -> Tournament:
    """Create a test online tournament."""
    tournament = Tournament(
        id=str(uuid.uuid4()),
        name="Test Online Tournament",
        description="A test tournament for online play",
        format=TournamentFormat.SWISS,
        total_rounds=3,
        current_round=1,
        time_control="10+5",
        status=TournamentStatus.ACTIVE,
        is_online=True,
        result_confirmation_minutes=10,
    )
    db_session.add(tournament)
    await db_session.commit()
    await db_session.refresh(tournament)
    return tournament


@pytest_asyncio.fixture
async def inperson_tournament(db_session: AsyncSession) -> Tournament:
    """Create a test in-person tournament."""
    tournament = Tournament(
        id=str(uuid.uuid4()),
        name="Test In-Person Tournament",
        description="A test tournament for OTB play",
        format=TournamentFormat.SWISS,
        total_rounds=3,
        current_round=1,
        time_control="15+10",
        status=TournamentStatus.ACTIVE,
        is_online=False,
        venue="Nairobi Chess Club",
        result_confirmation_minutes=10,
    )
    db_session.add(tournament)
    await db_session.commit()
    await db_session.refresh(tournament)
    return tournament


@pytest_asyncio.fixture
async def tournament_with_players(
    db_session: AsyncSession,
    inperson_tournament: Tournament,
    test_player: Player,
    test_player2: Player,
) -> Tournament:
    """Create tournament with registered players."""
    # Register players
    tp1 = TournamentPlayer(
        tournament_id=inperson_tournament.id,
        player_id=test_player.id,
        seed_rating=test_player.rapid_rating or 1500,
    )
    tp2 = TournamentPlayer(
        tournament_id=inperson_tournament.id,
        player_id=test_player2.id,
        seed_rating=test_player2.rapid_rating or 1500,
    )
    db_session.add_all([tp1, tp2])
    await db_session.commit()
    await db_session.refresh(inperson_tournament)
    return inperson_tournament


@pytest_asyncio.fixture
async def pending_pairing(
    db_session: AsyncSession,
    inperson_tournament: Tournament,
    test_player: Player,
    test_player2: Player,
) -> Pairing:
    """Create a pending pairing between two players."""
    # First register players
    tp1 = TournamentPlayer(
        tournament_id=inperson_tournament.id,
        player_id=test_player.id,
        seed_rating=1500,
    )
    tp2 = TournamentPlayer(
        tournament_id=inperson_tournament.id,
        player_id=test_player2.id,
        seed_rating=1600,
    )
    db_session.add_all([tp1, tp2])

    # Create pairing
    pairing = Pairing(
        id=str(uuid.uuid4()),
        tournament_id=inperson_tournament.id,
        round_number=1,
        board_number=1,
        white_player_id=test_player.id,
        black_player_id=test_player2.id,
        result=GameResult.PENDING,
        deadline=datetime.utcnow() + timedelta(hours=24),
    )
    db_session.add(pairing)
    await db_session.commit()
    await db_session.refresh(pairing)
    return pairing


@pytest_asyncio.fixture
async def online_pairing(
    db_session: AsyncSession,
    online_tournament: Tournament,
    test_player: Player,
    test_player2: Player,
) -> Pairing:
    """Create a pending pairing for online tournament."""
    # Register players
    tp1 = TournamentPlayer(
        tournament_id=online_tournament.id,
        player_id=test_player.id,
        seed_rating=1500,
    )
    tp2 = TournamentPlayer(
        tournament_id=online_tournament.id,
        player_id=test_player2.id,
        seed_rating=1600,
    )
    db_session.add_all([tp1, tp2])

    # Create pairing
    pairing = Pairing(
        id=str(uuid.uuid4()),
        tournament_id=online_tournament.id,
        round_number=1,
        board_number=1,
        white_player_id=test_player.id,
        black_player_id=test_player2.id,
        result=GameResult.PENDING,
        deadline=datetime.utcnow() + timedelta(hours=24),
    )
    db_session.add(pairing)
    await db_session.commit()
    await db_session.refresh(pairing)
    return pairing


def get_auth_header(player: Player) -> dict:
    """Generate auth header for a player."""
    token = AuthService.create_access_token(data={"sub": player.id})
    return {"Authorization": f"Bearer {token}"}
