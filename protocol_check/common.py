"""Helpers shared by the protocol check server and client.

They now live in the game's own package (fatrat/common.py and
fatrat/server/scoring.py); this module re-exports them so the protocol check
keeps working unchanged.
"""

from __future__ import annotations

from fatrat.common import (  # noqa: F401  (re-exported)
    TCP_PORT, UDP_PORT, detect_lan_ip, log, new_id, new_token, now_ms, say,
)
from fatrat.server.scoring import score  # noqa: F401
