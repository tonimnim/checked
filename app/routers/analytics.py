"""
Admin Analytics Router - Real historical data for dashboard charts
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from datetime import datetime, timedelta
from typing import List
from collections import defaultdict

from app.database import get_db
from app.models.player import Player
from app.models.tournament import Tournament
from app.models.pairing import Pairing

router = APIRouter(prefix="/api/admin/analytics", tags=["Admin Analytics"])

# In-memory traffic tracking (resets on server restart)
# In production, you'd want to persist this to database
traffic_data: dict[str, int] = defaultdict(int)


def record_request():
    """Record a request for the current hour"""
    hour_key = datetime.utcnow().strftime("%Y-%m-%d-%H")
    traffic_data[hour_key] += 1


@router.get("/user-growth")
async def get_user_growth(
    days: int = 30,
    db: AsyncSession = Depends(get_db)
):
    """
    Get user registration counts grouped by day.
    Returns data for the last N days.
    """
    # Calculate the start date
    start_date = datetime.utcnow() - timedelta(days=days)

    # Query to get registration counts by day
    # Using SQLite's date function
    query = text("""
        SELECT
            date(created_at) as registration_date,
            COUNT(*) as count
        FROM players
        WHERE created_at >= :start_date
        GROUP BY date(created_at)
        ORDER BY registration_date ASC
    """)

    result = await db.execute(query, {"start_date": start_date.isoformat()})
    rows = result.fetchall()

    # Create a dict of date -> count
    counts_by_date = {row[0]: row[1] for row in rows}

    # Fill in missing days with 0
    data = []
    current_date = start_date.date()
    end_date = datetime.utcnow().date()
    cumulative = 0

    # Get total users before start_date for cumulative count
    prior_count_query = text("""
        SELECT COUNT(*) FROM players WHERE created_at < :start_date
    """)
    prior_result = await db.execute(prior_count_query, {"start_date": start_date.isoformat()})
    cumulative = prior_result.scalar() or 0

    while current_date <= end_date:
        date_str = current_date.isoformat()
        daily_count = counts_by_date.get(date_str, 0)
        cumulative += daily_count

        data.append({
            "date": date_str,
            "name": current_date.strftime("%b %d"),  # e.g., "Jan 15"
            "new_users": daily_count,
            "total_users": cumulative
        })
        current_date += timedelta(days=1)

    return {
        "period_days": days,
        "data": data
    }


@router.get("/user-growth-weekly")
async def get_user_growth_weekly(
    db: AsyncSession = Depends(get_db)
):
    """
    Get user registration data for the last 7 days (for dashboard chart).
    """
    start_date = datetime.utcnow() - timedelta(days=6)  # Last 7 days including today

    query = text("""
        SELECT
            date(created_at) as registration_date,
            COUNT(*) as count
        FROM players
        WHERE created_at >= :start_date
        GROUP BY date(created_at)
        ORDER BY registration_date ASC
    """)

    result = await db.execute(query, {"start_date": start_date.isoformat()})
    rows = result.fetchall()

    counts_by_date = {row[0]: row[1] for row in rows}

    # Get cumulative count before the period
    prior_query = text("SELECT COUNT(*) FROM players WHERE created_at < :start_date")
    prior_result = await db.execute(prior_query, {"start_date": start_date.isoformat()})
    cumulative = prior_result.scalar() or 0

    data = []
    current_date = start_date.date()
    end_date = datetime.utcnow().date()

    while current_date <= end_date:
        date_str = current_date.isoformat()
        daily_count = counts_by_date.get(date_str, 0)
        cumulative += daily_count

        data.append({
            "name": current_date.strftime("%a"),  # e.g., "Mon"
            "date": date_str,
            "users": cumulative,
            "new": daily_count
        })
        current_date += timedelta(days=1)

    return data


@router.get("/traffic")
async def get_traffic_data(hours: int = 24):
    """
    Get API traffic data for the last N hours.
    Returns requests per hour.
    """
    data = []
    now = datetime.utcnow()

    for i in range(hours - 1, -1, -1):
        hour = now - timedelta(hours=i)
        hour_key = hour.strftime("%Y-%m-%d-%H")
        requests = traffic_data.get(hour_key, 0)

        data.append({
            "name": hour.strftime("%-I%p").lower() if hour.strftime("%p") else hour.strftime("%H:00"),
            "hour": hour_key,
            "requests": requests
        })

    return data


@router.get("/tournament-activity")
async def get_tournament_activity(
    days: int = 30,
    db: AsyncSession = Depends(get_db)
):
    """
    Get tournament creation and game activity over time.
    """
    start_date = datetime.utcnow() - timedelta(days=days)

    # Tournaments created by day
    tournaments_query = text("""
        SELECT
            date(created_at) as date,
            COUNT(*) as count
        FROM tournaments
        WHERE created_at >= :start_date
        GROUP BY date(created_at)
        ORDER BY date ASC
    """)

    # Games played by day
    games_query = text("""
        SELECT
            date(played_at) as date,
            COUNT(*) as count
        FROM pairings
        WHERE played_at IS NOT NULL AND played_at >= :start_date
        GROUP BY date(played_at)
        ORDER BY date ASC
    """)

    tournaments_result = await db.execute(tournaments_query, {"start_date": start_date.isoformat()})
    games_result = await db.execute(games_query, {"start_date": start_date.isoformat()})

    tournaments_by_date = {row[0]: row[1] for row in tournaments_result.fetchall()}
    games_by_date = {row[0]: row[1] for row in games_result.fetchall()}

    data = []
    current_date = start_date.date()
    end_date = datetime.utcnow().date()

    while current_date <= end_date:
        date_str = current_date.isoformat()
        data.append({
            "date": date_str,
            "name": current_date.strftime("%b %d"),
            "tournaments": tournaments_by_date.get(date_str, 0),
            "games": games_by_date.get(date_str, 0)
        })
        current_date += timedelta(days=1)

    return {
        "period_days": days,
        "data": data
    }


@router.get("/summary")
async def get_analytics_summary(
    db: AsyncSession = Depends(get_db)
):
    """
    Get summary statistics for the admin dashboard.
    """
    now = datetime.utcnow()
    today = now.date()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    # Total users
    total_users = await db.execute(select(func.count(Player.id)))
    total_users = total_users.scalar() or 0

    # New users this week
    new_users_week = await db.execute(
        select(func.count(Player.id)).where(Player.created_at >= week_ago)
    )
    new_users_week = new_users_week.scalar() or 0

    # New users this month
    new_users_month = await db.execute(
        select(func.count(Player.id)).where(Player.created_at >= month_ago)
    )
    new_users_month = new_users_month.scalar() or 0

    # Active tournaments
    active_tournaments = await db.execute(
        select(func.count(Tournament.id)).where(Tournament.status == "active")
    )
    active_tournaments = active_tournaments.scalar() or 0

    # Total tournaments
    total_tournaments = await db.execute(select(func.count(Tournament.id)))
    total_tournaments = total_tournaments.scalar() or 0

    # Games played this week
    games_week = await db.execute(
        select(func.count(Pairing.id)).where(
            Pairing.played_at.isnot(None),
            Pairing.played_at >= week_ago
        )
    )
    games_week = games_week.scalar() or 0

    # Calculate growth rate
    users_week_before = await db.execute(
        select(func.count(Player.id)).where(
            Player.created_at >= (week_ago - timedelta(days=7)),
            Player.created_at < week_ago
        )
    )
    users_week_before = users_week_before.scalar() or 0

    growth_rate = 0
    if users_week_before > 0:
        growth_rate = round(((new_users_week - users_week_before) / users_week_before) * 100, 1)
    elif new_users_week > 0:
        growth_rate = 100.0

    return {
        "total_users": total_users,
        "new_users_week": new_users_week,
        "new_users_month": new_users_month,
        "growth_rate": growth_rate,
        "active_tournaments": active_tournaments,
        "total_tournaments": total_tournaments,
        "games_this_week": games_week,
        "total_requests_today": sum(
            count for key, count in traffic_data.items()
            if key.startswith(today.isoformat())
        )
    }
