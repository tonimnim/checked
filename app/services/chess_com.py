import httpx
from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime

from app.config import get_settings

settings = get_settings()


class ChessComProfile(BaseModel):
    """Chess.com player profile data"""
    username: str
    avatar: Optional[str] = None
    player_id: Optional[int] = None
    url: Optional[str] = None
    name: Optional[str] = None
    country: Optional[str] = None
    joined: Optional[int] = None  # Unix timestamp
    last_online: Optional[int] = None
    status: Optional[str] = None  # premium, basic, staff, etc.


class ChessComStats(BaseModel):
    """Chess.com player stats (ratings)"""
    chess_rapid: Optional[int] = None
    chess_blitz: Optional[int] = None
    chess_bullet: Optional[int] = None
    chess_daily: Optional[int] = None
    tactics: Optional[int] = None
    puzzle_rush: Optional[int] = None


class ChessComGame(BaseModel):
    """Chess.com game data"""
    url: str
    pgn: Optional[str] = None
    time_control: Optional[str] = None
    time_class: Optional[str] = None  # rapid, blitz, bullet, daily
    rated: bool = True
    white_username: str
    white_rating: int
    white_result: str  # win, lose, draw, etc.
    black_username: str
    black_rating: int
    black_result: str
    end_time: Optional[int] = None


class ChessComService:
    """Service for interacting with Chess.com Public API"""

    def __init__(self):
        self.base_url = settings.chess_com_api_base
        self.headers = {
            "User-Agent": "ChessKenya/1.0 (contact@chesskenya.co.ke)"  # Chess.com requires User-Agent
        }

    async def get_player_profile(self, username: str) -> Optional[ChessComProfile]:
        """
        Fetch player profile from Chess.com
        Returns None if player doesn't exist
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/player/{username.lower()}",
                    headers=self.headers,
                    timeout=10.0
                )

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                data = response.json()

                return ChessComProfile(
                    username=data.get("username"),
                    avatar=data.get("avatar"),
                    player_id=data.get("player_id"),
                    url=data.get("url"),
                    name=data.get("name"),
                    country=data.get("country"),
                    joined=data.get("joined"),
                    last_online=data.get("last_online"),
                    status=data.get("status"),
                )

            except httpx.HTTPStatusError:
                return None
            except httpx.RequestError:
                raise Exception("Failed to connect to Chess.com API")

    async def get_player_stats(self, username: str) -> Optional[ChessComStats]:
        """
        Fetch player stats/ratings from Chess.com
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/player/{username.lower()}/stats",
                    headers=self.headers,
                    timeout=10.0
                )

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                data = response.json()

                # Extract ratings from different time controls
                rapid = data.get("chess_rapid", {}).get("last", {}).get("rating")
                blitz = data.get("chess_blitz", {}).get("last", {}).get("rating")
                bullet = data.get("chess_bullet", {}).get("last", {}).get("rating")
                daily = data.get("chess_daily", {}).get("last", {}).get("rating")
                tactics = data.get("tactics", {}).get("highest", {}).get("rating")
                puzzle_rush = data.get("puzzle_rush", {}).get("best", {}).get("score")

                return ChessComStats(
                    chess_rapid=rapid,
                    chess_blitz=blitz,
                    chess_bullet=bullet,
                    chess_daily=daily,
                    tactics=tactics,
                    puzzle_rush=puzzle_rush,
                )

            except httpx.HTTPStatusError:
                return None
            except httpx.RequestError:
                raise Exception("Failed to connect to Chess.com API")

    async def get_country_players(self, country_code: str = "KE") -> Optional[List[str]]:
        """
        Fetch list of players from a country (default: Kenya)
        Uses ISO 3166 2-character country code
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/country/{country_code.upper()}/players",
                    headers=self.headers,
                    timeout=15.0
                )

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                data = response.json()

                return data.get("players", [])

            except httpx.HTTPStatusError:
                return None
            except httpx.RequestError:
                raise Exception("Failed to connect to Chess.com API")

    async def get_titled_players(self, title: str = "FM") -> Optional[List[str]]:
        """
        Fetch list of titled players
        Valid titles: GM, WGM, IM, WIM, FM, WFM, NM, WNM, CM, WCM
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/titled/{title.upper()}",
                    headers=self.headers,
                    timeout=15.0
                )

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                data = response.json()

                return data.get("players", [])

            except httpx.HTTPStatusError:
                return None
            except httpx.RequestError:
                raise Exception("Failed to connect to Chess.com API")

    async def verify_username(self, username: str) -> bool:
        """
        Verify if a Chess.com username exists
        """
        profile = await self.get_player_profile(username)
        return profile is not None

    async def get_player_games(
        self, username: str, year: int, month: int
    ) -> Optional[List[ChessComGame]]:
        """
        Fetch player's games for a specific month
        Useful for verifying game results
        """
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.base_url}/player/{username.lower()}/games/{year}/{month:02d}",
                    headers=self.headers,
                    timeout=15.0
                )

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                data = response.json()

                games = []
                for g in data.get("games", []):
                    games.append(ChessComGame(
                        url=g.get("url", ""),
                        pgn=g.get("pgn"),
                        time_control=g.get("time_control"),
                        time_class=g.get("time_class"),
                        rated=g.get("rated", True),
                        white_username=g.get("white", {}).get("username", ""),
                        white_rating=g.get("white", {}).get("rating", 0),
                        white_result=g.get("white", {}).get("result", ""),
                        black_username=g.get("black", {}).get("username", ""),
                        black_rating=g.get("black", {}).get("rating", 0),
                        black_result=g.get("black", {}).get("result", ""),
                        end_time=g.get("end_time"),
                    ))

                return games

            except httpx.HTTPStatusError:
                return None
            except httpx.RequestError:
                raise Exception("Failed to connect to Chess.com API")

    async def find_game_between_players(
        self,
        player1: str,
        player2: str,
        time_class: str = "rapid",
        after_timestamp: Optional[int] = None
    ) -> Optional[ChessComGame]:
        """
        Find a game between two players.
        Searches recent games of player1 for a match against player2.

        Args:
            player1: First player's Chess.com username
            player2: Second player's Chess.com username
            time_class: rapid, blitz, bullet, or daily
            after_timestamp: Only consider games after this Unix timestamp

        Returns:
            The most recent matching game, or None
        """
        now = datetime.utcnow()
        year = now.year
        month = now.month

        # Check current month and previous month
        for _ in range(2):
            games = await self.get_player_games(player1, year, month)
            if games:
                # Filter and sort by end_time (most recent first)
                matching = [
                    g for g in games
                    if (g.white_username.lower() == player2.lower() or
                        g.black_username.lower() == player2.lower())
                    and g.time_class == time_class
                    and (after_timestamp is None or (g.end_time and g.end_time > after_timestamp))
                ]

                if matching:
                    # Return most recent
                    return max(matching, key=lambda x: x.end_time or 0)

            # Go to previous month
            month -= 1
            if month == 0:
                month = 12
                year -= 1

        return None

    def parse_game_result(self, game: ChessComGame, player_username: str) -> str:
        """
        Parse game result for a specific player.

        Returns:
            'win', 'loss', or 'draw'
        """
        is_white = game.white_username.lower() == player_username.lower()
        result = game.white_result if is_white else game.black_result

        # Chess.com result values
        win_results = ['win', 'checkmated', 'timeout', 'resigned', 'abandoned']
        draw_results = ['agreed', 'stalemate', 'repetition', 'insufficient', '50move', 'timevsinsufficient']

        if result == 'win':
            return 'win'
        elif result in draw_results or 'draw' in result.lower():
            return 'draw'
        else:
            return 'loss'


    async def get_game_by_url(self, game_url: str) -> Optional[dict]:
        """
        Fetch game data from a Chess.com game URL.

        Accepts URLs like:
        - https://www.chess.com/game/164204596142
        - https://www.chess.com/game/live/164204596142
        - https://www.chess.com/live#g=164204596142

        Returns raw game data dict or None if not found.
        """
        import re

        # Extract game ID from various URL formats
        game_id = None

        # Pattern 1: /game/12345 or /game/live/12345
        match = re.search(r'/game(?:/live)?/(\d+)', game_url)
        if match:
            game_id = match.group(1)

        # Pattern 2: #g=12345
        if not game_id:
            match = re.search(r'[#?&]g=(\d+)', game_url)
            if match:
                game_id = match.group(1)

        # Pattern 3: Just the ID
        if not game_id and game_url.isdigit():
            game_id = game_url

        if not game_id:
            return None

        # Fetch from Chess.com callback endpoint
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"https://www.chess.com/callback/live/game/{game_id}",
                    headers=self.headers,
                    timeout=15.0
                )

                if response.status_code == 404:
                    return None

                response.raise_for_status()
                return response.json()

            except httpx.HTTPStatusError:
                return None
            except httpx.RequestError:
                return None

    async def verify_game_result(
        self,
        game_url: str,
        expected_white: str,
        expected_black: str,
        pairing_created_at: Optional[datetime] = None
    ) -> dict:
        """
        Verify a game result from Chess.com.

        Args:
            game_url: Chess.com game URL
            expected_white: Expected white player's Chess.com username
            expected_black: Expected black player's Chess.com username
            pairing_created_at: When the pairing was created (to verify game was played after)

        Returns:
            {
                "valid": True/False,
                "error": "error message if invalid",
                "result": "white_wins" / "black_wins" / "draw",
                "white_username": "actual white player",
                "black_username": "actual black player",
                "game_id": "123456",
                "time_control": "600",
                "played_at": datetime
            }
        """
        game_data = await self.get_game_by_url(game_url)

        if not game_data:
            return {
                "valid": False,
                "error": "Could not fetch game from Chess.com. Check the URL."
            }

        # Extract player info
        players = game_data.get("players", {})
        white_data = players.get("top", {}) if players.get("top", {}).get("color") == "white" else players.get("bottom", {})
        black_data = players.get("bottom", {}) if players.get("bottom", {}).get("color") == "black" else players.get("top", {})

        # Handle different response structures
        if not white_data.get("color"):
            # Try alternate structure
            white_data = players.get("white", players.get("top", {}))
            black_data = players.get("black", players.get("bottom", {}))

        white_username = white_data.get("username", "").lower()
        black_username = black_data.get("username", "").lower()

        # Verify players match (allow swapped colors)
        expected_white_lower = expected_white.lower()
        expected_black_lower = expected_black.lower()

        players_match = (
            (white_username == expected_white_lower and black_username == expected_black_lower) or
            (white_username == expected_black_lower and black_username == expected_white_lower)
        )

        if not players_match:
            return {
                "valid": False,
                "error": f"Players don't match. Game has {white_username} vs {black_username}, expected {expected_white} vs {expected_black}"
            }

        # Check if game is finished
        game_status = game_data.get("game", {}).get("status", "")
        if game_status not in ["finished", "resolved"]:
            return {
                "valid": False,
                "error": f"Game is not finished (status: {game_status})"
            }

        # Extract result
        white_result = white_data.get("result", "")
        black_result = black_data.get("result", "")

        # Determine winner
        result = None
        if white_result == "win" or black_result in ["checkmated", "timeout", "resigned", "abandoned"]:
            result = "white_wins"
        elif black_result == "win" or white_result in ["checkmated", "timeout", "resigned", "abandoned"]:
            result = "black_wins"
        elif white_result in ["draw", "agreed", "stalemate", "repetition", "insufficient", "50move", "timevsinsufficient"]:
            result = "draw"
        elif black_result in ["draw", "agreed", "stalemate", "repetition", "insufficient", "50move", "timevsinsufficient"]:
            result = "draw"

        if not result:
            return {
                "valid": False,
                "error": f"Could not determine result. White: {white_result}, Black: {black_result}"
            }

        # If colors were swapped, adjust result for our pairing
        colors_swapped = (white_username == expected_black_lower)
        if colors_swapped:
            if result == "white_wins":
                result = "black_wins"
            elif result == "black_wins":
                result = "white_wins"

        # Extract game timestamp
        end_time = game_data.get("game", {}).get("endTime")
        played_at = None
        if end_time:
            played_at = datetime.fromtimestamp(end_time / 1000) if end_time > 9999999999 else datetime.fromtimestamp(end_time)

        # Verify game was played after pairing was created
        if pairing_created_at and played_at:
            if played_at < pairing_created_at:
                return {
                    "valid": False,
                    "error": f"Game was played before the pairing was created. Game: {played_at}, Pairing: {pairing_created_at}"
                }

        return {
            "valid": True,
            "result": result,
            "white_username": expected_white if not colors_swapped else expected_black,
            "black_username": expected_black if not colors_swapped else expected_white,
            "actual_white": white_username,
            "actual_black": black_username,
            "game_id": str(game_data.get("game", {}).get("id", "")),
            "time_control": game_data.get("game", {}).get("timeControl"),
            "played_at": played_at
        }


# Singleton instance
chess_com_service = ChessComService()
