"""Scoring and ranking rules (report sections 2.2 and 2.4).

Pure functions: no sockets, no locks, no clock. The room calls them while
holding its lock, and the unit tests call them directly.
"""

from __future__ import annotations

from typing import Iterable, List, Tuple


def score(remaining_ms: int, limit_ms: int) -> int:
    """Points for a correct answer: 500 to 1000, more for a faster answer.

    Inputs:
      remaining_ms  time left on the server's clock when the answer was accepted
      limit_ms      the question's time limit
    Output: round(500 + 500 * remaining / limit), rounding halves up.

    Only integers are used, so every machine computes the same result:
    500 + (1000*r + L) // (2*L) equals floor(500 + 500*r/L + 0.5).
    A wrong or missing answer scores 0; the caller handles that.
    """
    r = max(0, min(remaining_ms, limit_ms))
    return 500 + (1000 * r + limit_ms) // (2 * limit_ms)


def leaderboard(players: Iterable[Tuple[int, str, str, str]]) -> List[dict]:
    """Build the RankRow list sent in every STATE.

    Input: (total_score, username, player_id, participation) for every player
    who ever joined the room, including players who left or were removed.
    Output: rows ordered by descending score. Ranks are dense (1, 2, 3, ...):
    players with the same score share one row, and the next different score
    gets the next rank. Names inside a row are sorted so the order is stable.
    """
    everyone = list(players)
    rows: List[dict] = []
    for points in sorted({p[0] for p in everyone}, reverse=True):
        tied = sorted((p for p in everyone if p[0] == points), key=lambda p: (p[1].casefold(), p[2]))
        rows.append({
            "rank": len(rows) + 1,
            "score": points,
            "players": [{"player_id": pid, "username": name, "participation": part}
                        for _, name, pid, part in tied],
        })
    return rows
