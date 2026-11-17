"""Fat-Rat game server.

main         - sockets, the accept loop and one reader thread per TCP connection
connection   - one TCP connection: ordered outgoing queue and its writer thread
registry     - all rooms: PINs, host ownership, the creation ledger (registry lock)
room         - one room: members, quiz, question attempt, answers, scores (room lock)
projection   - the STATE snapshot built for one recipient
scoring      - the score formula and the leaderboard (pure functions)
timer        - one thread per room: question deadline and LIVE_STATUS ticks
udp_service  - the UDP socket: UDP_HELLO registration and LIVE_STATUS sending

Start it with:  python3 -m fatrat.server
"""
