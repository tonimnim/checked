"""
Chess Tournament Pairing Algorithms

Supports:
1. Swiss System - For large tournaments, players with similar scores paired together
2. Round Robin - Everyone plays everyone, best for small tournaments (≤12 players)
"""
from typing import List, Tuple, Optional, Dict, Set
from dataclasses import dataclass
from copy import copy


@dataclass
class SwissPlayer:
    """Player data needed for Swiss pairing"""
    id: str
    score: float
    rating: int
    games_as_white: int
    games_as_black: int
    opponents: Set[str]  # Set of opponent IDs already played
    is_withdrawn: bool = False

    @property
    def color_balance(self) -> int:
        """Positive = more white games, negative = more black games"""
        return self.games_as_white - self.games_as_black

    @property
    def needs_white(self) -> bool:
        """True if player should get white (played more black)"""
        return self.color_balance < 0

    @property
    def needs_black(self) -> bool:
        """True if player should get black (played more white)"""
        return self.color_balance > 0


@dataclass
class SwissPairing:
    """Result of pairing two players"""
    white_id: str
    black_id: str
    board_number: int
    is_bye: bool = False


class SwissPairingEngine:
    """
    Swiss pairing algorithm implementation.

    For round 1: Pair by rating (top half vs bottom half)
    For round 2+: Pair by score groups, avoiding rematches
    """

    def __init__(self, players: List[SwissPlayer]):
        self.players = [p for p in players if not p.is_withdrawn]
        self.pairings: List[SwissPairing] = []

    def generate_round_1_pairings(self) -> List[SwissPairing]:
        """
        Generate pairings for round 1 using the standard method:
        Sort by rating, split into top and bottom halves,
        pair top[0] vs bottom[0], top[1] vs bottom[1], etc.
        Top half gets white.
        """
        self.pairings = []

        # Sort by rating (highest first)
        sorted_players = sorted(self.players, key=lambda p: p.rating, reverse=True)

        n = len(sorted_players)

        # Handle odd number of players (give bye to lowest rated)
        if n % 2 == 1:
            bye_player = sorted_players.pop()  # Lowest rated gets bye
            self.pairings.append(SwissPairing(
                white_id=bye_player.id,
                black_id="",  # No opponent
                board_number=0,
                is_bye=True
            ))
            n -= 1

        # Split into halves
        half = n // 2
        top_half = sorted_players[:half]
        bottom_half = sorted_players[half:]

        # Pair them
        for i, (top, bottom) in enumerate(zip(top_half, bottom_half)):
            self.pairings.append(SwissPairing(
                white_id=top.id,  # Higher rated gets white in round 1
                black_id=bottom.id,
                board_number=i + 1
            ))

        return self.pairings

    def generate_pairings(self, round_number: int) -> List[SwissPairing]:
        """
        Generate pairings for any round.
        Round 1 uses rating-based pairing.
        Round 2+ uses score-based pairing.
        """
        if round_number == 1:
            return self.generate_round_1_pairings()

        return self._generate_swiss_pairings()

    def _generate_swiss_pairings(self) -> List[SwissPairing]:
        """
        Generate Swiss pairings for rounds 2+.
        Groups players by score, then pairs within groups.
        """
        self.pairings = []

        # Sort by score (descending), then by rating (descending) for tiebreak
        sorted_players = sorted(
            self.players,
            key=lambda p: (p.score, p.rating),
            reverse=True
        )

        paired_ids: Set[str] = set()
        unpaired: List[SwissPlayer] = []

        # Group by score
        score_groups: Dict[float, List[SwissPlayer]] = {}
        for player in sorted_players:
            if player.score not in score_groups:
                score_groups[player.score] = []
            score_groups[player.score].append(player)

        # Process score groups from highest to lowest
        scores = sorted(score_groups.keys(), reverse=True)

        for score in scores:
            group = score_groups[score]
            # Add any unpaired from previous group
            group = unpaired + group
            unpaired = []

            # Pair within group
            available = [p for p in group if p.id not in paired_ids]

            while len(available) >= 2:
                player1 = available.pop(0)
                paired = False

                # Find best opponent
                for i, player2 in enumerate(available):
                    # Skip if already played each other
                    if player2.id in player1.opponents:
                        continue

                    # Create pairing with color balance
                    white_id, black_id = self._assign_colors(player1, player2)

                    board_num = len(self.pairings) + 1
                    self.pairings.append(SwissPairing(
                        white_id=white_id,
                        black_id=black_id,
                        board_number=board_num
                    ))

                    paired_ids.add(player1.id)
                    paired_ids.add(player2.id)
                    available.pop(i)
                    paired = True
                    break

                if not paired:
                    # Couldn't find opponent in this group, carry to next
                    unpaired.append(player1)

            # Remaining unpaired go to next score group
            unpaired.extend(available)

        # Handle bye if odd number
        if unpaired:
            # Give bye to lowest scored unpaired player
            bye_player = min(unpaired, key=lambda p: (p.score, p.rating))
            self.pairings.append(SwissPairing(
                white_id=bye_player.id,
                black_id="",
                board_number=0,
                is_bye=True
            ))

        return self.pairings

    def _assign_colors(self, p1: SwissPlayer, p2: SwissPlayer) -> Tuple[str, str]:
        """
        Assign colors (white/black) based on color balance.
        Returns (white_id, black_id)
        """
        # If one player strongly needs a color, give it to them
        if p1.needs_white and not p2.needs_white:
            return (p1.id, p2.id)
        if p2.needs_white and not p1.needs_white:
            return (p2.id, p1.id)
        if p1.needs_black and not p2.needs_black:
            return (p2.id, p1.id)
        if p2.needs_black and not p1.needs_black:
            return (p1.id, p2.id)

        # If both have same color balance, higher rated gets white
        if p1.rating >= p2.rating:
            return (p1.id, p2.id)
        else:
            return (p2.id, p1.id)


class RoundRobinEngine:
    """
    Round Robin pairing algorithm.

    Every player plays every other player exactly once.
    Uses the "circle method" (Berger tables) for scheduling.

    Best for small tournaments (4-12 players).
    Total rounds = N-1 (even players) or N (odd players with bye)
    Total games = N*(N-1)/2
    """

    def __init__(self, players: List[SwissPlayer]):
        self.players = [p for p in players if not p.is_withdrawn]
        self.n = len(self.players)

        # Sort by rating for seeding
        self.players = sorted(self.players, key=lambda p: p.rating, reverse=True)

        # If odd number, add a "BYE" placeholder
        self.has_bye = self.n % 2 == 1
        if self.has_bye:
            self.players.append(SwissPlayer(
                id="BYE",
                score=0,
                rating=0,
                games_as_white=0,
                games_as_black=0,
                opponents=set()
            ))
            self.n += 1

    def get_total_rounds(self) -> int:
        """Return total number of rounds needed"""
        return self.n - 1

    def generate_all_rounds(self) -> Dict[int, List[SwissPairing]]:
        """
        Generate pairings for ALL rounds at once.
        Returns dict: {round_number: [pairings]}
        """
        all_rounds = {}

        # Create player indices for rotation
        # Fix first player, rotate the rest (circle method)
        indices = list(range(self.n))

        for round_num in range(1, self.n):
            round_pairings = []
            board = 1

            # Pair first half with second half (reversed)
            for i in range(self.n // 2):
                p1_idx = indices[i]
                p2_idx = indices[self.n - 1 - i]

                player1 = self.players[p1_idx]
                player2 = self.players[p2_idx]

                # Handle BYE
                if player1.id == "BYE":
                    round_pairings.append(SwissPairing(
                        white_id=player2.id,
                        black_id="",
                        board_number=0,
                        is_bye=True
                    ))
                    continue
                elif player2.id == "BYE":
                    round_pairings.append(SwissPairing(
                        white_id=player1.id,
                        black_id="",
                        board_number=0,
                        is_bye=True
                    ))
                    continue

                # Alternate colors by round for fairness
                if round_num % 2 == 1:
                    white, black = player1, player2
                else:
                    white, black = player2, player1

                # Additional color balancing
                if i % 2 == 1:
                    white, black = black, white

                round_pairings.append(SwissPairing(
                    white_id=white.id,
                    black_id=black.id,
                    board_number=board
                ))
                board += 1

            all_rounds[round_num] = round_pairings

            # Rotate: keep first player fixed, rotate others
            # [0, 1, 2, 3, 4, 5] -> [0, 5, 1, 2, 3, 4]
            indices = [indices[0]] + [indices[-1]] + indices[1:-1]

        return all_rounds

    def generate_round(self, round_number: int) -> List[SwissPairing]:
        """Generate pairings for a specific round"""
        all_rounds = self.generate_all_rounds()
        return all_rounds.get(round_number, [])


def calculate_buchholz(
    player_id: str,
    all_players: Dict[str, SwissPlayer],
    opponents: Set[str]
) -> float:
    """
    Calculate Buchholz tiebreaker.
    Sum of all opponents' scores.
    """
    total = 0.0
    for opp_id in opponents:
        if opp_id in all_players:
            total += all_players[opp_id].score
    return total


def calculate_sonneborn_berger(
    player_id: str,
    all_players: Dict[str, SwissPlayer],
    game_results: Dict[str, str]  # opponent_id -> 'win'/'loss'/'draw'
) -> float:
    """
    Calculate Sonneborn-Berger tiebreaker.
    Sum of (opponent score * game points against them)
    Win = 1, Draw = 0.5, Loss = 0
    """
    total = 0.0
    for opp_id, result in game_results.items():
        if opp_id not in all_players:
            continue

        opp_score = all_players[opp_id].score
        if result == 'win':
            total += opp_score * 1.0
        elif result == 'draw':
            total += opp_score * 0.5
        # Loss adds nothing

    return total
