from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from app.config import get_settings

settings = get_settings()

# SQLite production optimizations
SQLITE_PRAGMAS = [
    ("journal_mode", "WAL"),          # Write-Ahead Logging - concurrent reads during writes
    ("synchronous", "NORMAL"),         # Balance of safety and speed (FULL for max safety)
    ("cache_size", "-64000"),          # 64MB cache (negative = KB)
    ("busy_timeout", "30000"),         # Wait 30s on lock (handles burst traffic)
    ("foreign_keys", "ON"),            # Enforce referential integrity
    ("temp_store", "MEMORY"),          # Store temp tables in memory
    ("mmap_size", "268435456"),        # 256MB memory-mapped I/O
    ("page_size", "4096"),             # Optimal page size
    ("wal_autocheckpoint", "1000"),    # Checkpoint every 1000 pages (~4MB)
]


def set_sqlite_pragmas(dbapi_conn, connection_record):
    """Set SQLite pragmas for production performance"""
    cursor = dbapi_conn.cursor()
    for pragma, value in SQLITE_PRAGMAS:
        cursor.execute(f"PRAGMA {pragma}={value};")
    cursor.close()


# Create engine with optimizations
# Note: Pool settings don't apply to SQLite in-memory databases
_is_sqlite_memory = ":memory:" in settings.database_url or "mode=memory" in settings.database_url

if _is_sqlite_memory:
    # In-memory SQLite (testing) - use StaticPool
    from sqlalchemy.pool import StaticPool
    engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
else:
    # Production/file-based database - use connection pooling
    engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        pool_size=20,              # Base connections
        max_overflow=30,           # Extra connections under load
        pool_timeout=30,           # Wait time for connection
        pool_recycle=1800,         # Recycle connections every 30 min
        pool_pre_ping=True,        # Verify connections before use
    )

# Apply SQLite pragmas on every connection (skip for in-memory test databases)
if "sqlite" in settings.database_url and not _is_sqlite_memory:
    @event.listens_for(engine.sync_engine, "connect")
    def on_connect(dbapi_conn, connection_record):
        set_sqlite_pragmas(dbapi_conn, connection_record)


async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Initialize database and create tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Verify WAL mode is active
    if "sqlite" in settings.database_url:
        async with async_session_maker() as session:
            result = await session.execute(text("PRAGMA journal_mode;"))
            mode = result.scalar()
            print(f"SQLite journal mode: {mode}")


async def optimize_db():
    """Run periodic optimization (call this in a background task)"""
    if "sqlite" not in settings.database_url:
        return

    async with async_session_maker() as session:
        # Analyze tables for query optimizer
        await session.execute(text("ANALYZE;"))
        # Optimize WAL file
        await session.execute(text("PRAGMA wal_checkpoint(TRUNCATE);"))
        await session.commit()


async def get_db_stats():
    """Get database statistics for monitoring"""
    if "sqlite" not in settings.database_url:
        return {}

    async with async_session_maker() as session:
        stats = {}

        # Database size
        result = await session.execute(text("SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size();"))
        stats["db_size_bytes"] = result.scalar()

        # WAL size
        result = await session.execute(text("PRAGMA wal_checkpoint;"))
        row = result.fetchone()
        if row:
            stats["wal_frames"] = row[1] if len(row) > 1 else 0

        # Table counts
        tables = ["players", "tournaments", "tournament_players", "pairings", "login_history", "security_flags", "device_fingerprints"]
        for table in tables:
            try:
                result = await session.execute(text(f"SELECT COUNT(*) FROM {table};"))
                stats[f"{table}_count"] = result.scalar()
            except:
                stats[f"{table}_count"] = 0

        return stats
