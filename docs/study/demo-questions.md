# Demo questions and answers

Started 2026-11-16, updated 2026-11-17; grows as the project does. The live demo is 50% of the grade and each member is questioned alone. Practise saying each answer aloud, then open the file named and show it. Background for all of these: [01-how-fat-rat-works.md](01-how-fat-rat-works.md).

## Protocol

**What does a message look like?**
JSON with `v`, `type` and `data`, plus `request_id` on requests and `reply_to` on replies. On TCP it is preceded by a 4-byte length. `fatrat/protocol/catalogue.py` lists all 32 types.

**How do you know where a TCP message ends?**
The first 4 bytes give the length of the JSON that follows. `framing.py: recv_frame` reads exactly that many bytes, looping because `recv` may return fewer.

**What if one read contains half a message, or two messages?**
`_recv_exact` loops until it has the exact count, so half a message just needs more reads. It never reads past the end of a frame, so the second message stays in the socket buffer for the next call. `tests/test_framing.py` tests both.

**How does a client match a reply to its request?**
Each request has a new UUID in `request_id`; the reply repeats it in `reply_to`. `client/core.py: request` waits for that ID while other messages keep arriving.

**What happens with an invalid message?**
Well-formed but wrong: an `ERROR` with a code, connection stays open. Broken framing or JSON: that connection is closed, because the position in the byte stream can no longer be trusted. `server/main.py: handle_connection`.

## TCP and UDP

**Why both?**
TCP for everything that changes the game, because it must arrive, once, in order. UDP for the countdown and answered count, because each snapshot replaces the last and a lost one does not matter.

**What if a UDP datagram is lost, repeated or arrives late?**
Lost: ignored, the next one replaces it, and the client counts down locally. Repeated or late: its `seq` is not greater than the last accepted, so it is dropped. `protocol/udp.py: LiveStatusFilter.check`.

**What if UDP is blocked completely?**
The game still works. Questions open and close by TCP. After 3 s without a snapshot the client refreshes once over TCP with `GET_STATE`. `client/core.py: _check_udp_quiet`.

**Could a player cheat by sending a fake time or score?**
No. `SUBMIT_ANSWER` carries only IDs. The server uses its own clock at the moment it accepts the answer, under the room lock, and computes the score itself. `server/room.py: _submit`, `_close`.

**How does the server know which UDP address is which player?**
The player gets a random nonce over TCP and sends it back over UDP. The datagram must come from the same IP as the player's TCP connection. `room.py: bind_udp`, `confirm_udp`.

## Threads and synchronization

**Which threads exist?**
Server: one accepting, a reader and a writer per client, a timer per room, one for UDP. Table in section 6 of the walkthrough.

**What is shared, and how is it protected?**
Each room's state by that room's lock. The room tables and host ownership by the registry lock. Each connection's outgoing frames by a queue with a single writer thread.

**How do you know your locks actually matter?**
We removed one on purpose and ran the test again. Eight threads create a room for the same host at once: with the registry lock, one room; without it, eight rooms and a failing test. `tests/test_room.py: test_simultaneous_creates...`, `docs/test-results/2026-11-17/planted-bug.txt`.

**Show me a race condition you prevented.**
Three players joining a room with one free slot. `Room.join` counts and adds under the room lock, so exactly one gets in. Check C13 in `protocol_check/rooms_client.py` races three joins and expects one success.

**Another one?**
An answer arriving at the same instant the timer closes the question. Both take the room lock; whichever is second sees the result of the first. Every handler also checks the deadline itself (`Room._expire`), so a slow timer cannot let a late answer in.

**How do you avoid deadlock?**
One fixed order: registry lock, then room lock, never the reverse. And no thread ever holds two room locks.

**Why does the server not write to sockets while holding a lock?**
A slow client would block the write and freeze the whole room. Replies are queued under the lock and written by the connection's writer thread. `server/connection.py`.

## Errors and robustness

**What happens when a player's Wi-Fi drops mid-question?**
The player stays in the room, keeps score and slot, and still counts as someone who has not answered. The client reconnects automatically and resumes with its token. `Room.disconnected`, `Room.resume`, `client/core.py: _reconnect_loop`.

**And when the host drops?**
An open question is paused so the clock does not run with nobody in charge. The host reconnects and resumes explicitly. `tests/test_game.py: test_host_drop_pauses...`.

**Can one bad client crash the server or another room?**
No. Each connection has its own reader thread that catches its own errors and closes only its own socket. `tests/test_game.py: test_bad_bytes_close_only_the_offending_connection`.

**What stops one host from opening many rooms?**
A saved host token. The registry allows one unfinished room per token and answers `HOST_HAS_ROOM` with the existing room's ID. `server/registry.py: create`.

**How do you notice a client that just disappears, without closing the connection?**
TCP alone does not tell you while nothing is being sent. The server sends `PING` after 5 quiet seconds and closes the connection if no `PONG` comes within 15 s. `server/main.py: heartbeat_loop`.

**What stops a client from flooding the server?**
Each connection has a token bucket: 10 requests per second, burst of 20. Beyond that the server answers `RATE_LIMITED` with how long to wait, and does nothing else. `server/connection.py: allow_request`.

**The host edits a question while players are answering. What happens to their answers?**
The question is paused, then restarted as a new attempt with full time. Old answers are discarded and not scored, because they were given to different content. `room.py: _do_edit_commit`.

**The reply to CREATE_ROOM is lost. Does the host end up with two rooms?**
No. The host program saves the request ID on disk before sending. Started again, it sends the same request; the server remembers every successful creation (the ledger) and answers `ROOM_ALREADY_CREATED` with the room's ID, and the host resumes it. `client/storage.py: pending_create`, `server/registry.py: create`, `tests/test_game.py: test_lost_create_reply...`.

**The host ends the room at the exact moment the question's time runs out. What happens?**
The deadline is applied first: the question closes and is scored. That changes `control_rev`, so the host's command, decided on the old screen, is refused with `STALE_STATE` and a fresh STATE. The host confirms again and the room ends with the points kept. `tests/test_room.py: test_close_room_at_a_passed_deadline...`.

**What if UDP is blocked, not just lossy?**
After 3 s with no snapshot the client asks once over TCP (`GET_STATE`) and shows the countdown as approximate. Questions still open and close by TCP. `client/core.py: _check_udp_quiet`, `tests/test_game.py: test_udp_silence...`.

**How can you show UDP loss and reordering if the LAN does not lose anything?**
Start the server with `--impair-udp`. It then drops every 4th datagram, duplicates every 5th and delays every 7th. The client's counters (`udp_dropped`) show what it rejected, and the game is unaffected. `tests/test_game.py: test_lost_duplicated_and_reordered_udp...`.

## Testing

**How did you test it?**
Three levels: unit tests of the protocol and of the room engine with a fake clock (`tests/`), end-to-end tests with real sockets (`tests/test_game.py`), and scripted clients that run 50 players and three rooms at once (`protocol_check/rooms_client.py`, `fatrat/client/bot.py`). Raw outputs are in `docs/test-results/`.
