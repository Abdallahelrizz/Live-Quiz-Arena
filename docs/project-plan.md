# Fat-Rat: plan for everything that is left

## Context

Sections 1–2 of the report are submitted and the protocol layer (`fatrat/protocol/`) is built and tested. Nothing else exists: no game server, no clients, no interface, no report §3–§6, no second-device run, no Wireshark captures, no demo preparation. The final submission and live demo are due in about two weeks (around **30 November 2026**; today is 16 November).

Three things shape the plan:

- **Backend first.** The instructor would appreciate an extraordinary GUI, but the PDF gives the GUI no credit by itself, and every graded criterion is about the protocol, TCP/UDP, threads, synchronization, robustness and testing. The GUI is built only on a backend that already works from the terminal.
- **The GUI is designed in Claude Design** (Abdallah's choice). So the client is split into a network core with no interface code and a thin interface on top. The design work can run in parallel with the backend because it only needs the screen list in section 6.
- **The demo is 50% and individual, and nobody on the team can explain the system yet.** So the code is written to be explained (small files, one job each, simple thread model), and section 9 is a study track, not an afterthought.

The PDF also says a correct, well-documented application beats an ambitious incomplete one. The work is therefore staged: after every stage the project is runnable and submittable, and anything not reached is declared in report §5.

## Section 0 — Housekeeping (day 1, 30 minutes)

- Restore `output/docx/CSC430_Fat_Rat_Sections_1_and_2.docx` from commit `1c54dc1`. It was deleted in the 10 November commit, and the two tests in `tests/test_report_consistency.py` are silently skipped without it.
- Confirm `python3 -m unittest` shows 57 run, 0 skipped.
- Ask the instructor two questions early (section 6 depends on the second):
  1. the exact submission and demo dates;
  2. whether the interface may be HTML rendered inside the Python client's own window, with all networking still raw Python sockets.

## Section 1 — Server core: the required game (days 1–4)

New package `fatrat/server/`, standard library only, built on `fatrat/protocol/`.

| File | Job |
|---|---|
| `main.py` | Command line, bind TCP 43000 and UDP 43001, print the LAN address, accept loop, clean shutdown on Ctrl+C. |
| `connection.py` | One TCP connection: a reader thread and a writer thread with a bounded queue. A slow client is disconnected instead of blocking its room. |
| `registry.py` | All rooms: PIN allocation, host ownership (one unfinished room per host token), creation ledger. Guarded by the registry lock. |
| `room.py` | One room: members, quiz, phase, current attempt, answers, scores. One lock per room. Handlers take a validated message and return the list of messages to send; no socket calls inside the lock. |
| `scoring.py` | Pure functions: score formula, dense-rank leaderboard with ties grouped. |
| `projection.py` | Builds the STATE for one recipient (host sees text and quiz; player sees symbols only). |
| `timer.py` | One timer thread per room: closes the question at its deadline and sends LIVE_STATUS about once per second. |
| `udp_service.py` | One UDP thread: receives UDP_HELLO, checks nonce and source address, sends LIVE_STATUS. |

Reuse, do not rewrite:
- `fatrat/protocol/framing.py` (`recv_message`, `send_message`), `messages.py` (`validate_message`, `validate_quiz`, `ProtocolError`), `udp.py` (`encode_datagram`, `parse_datagram`), `catalogue.py` (limits, error codes).
- The writer-queue pattern of `protocol_check/server.py` (`Connection`), and the room flow of `protocol_check/rooms.py` (create, join with the 50 cap, start gate, answers, close, next) as the starting point for `room.py`.
- `protocol_check/common.py`: `score`, `detect_lan_ip`, `now_ms`, `new_token` move into `fatrat/` and are imported back.

Messages in this stage: CREATE_ROOM, JOIN_ROOM, PUT_QUIZ, START_QUIZ, SUBMIT_ANSWER, CLOSE_QUESTION, NEXT_QUESTION, LEAVE_ROOM, GET_STATE, REQUEST_UDP_BIND, UDP_HELLO, UDP_BOUND, LIVE_STATUS, RESULT, ERROR, STATE. This covers functional requirements 1–7.

Thread model, kept simple enough to say in one breath: one accept thread, two threads per client (read, write), one timer thread per room, one UDP thread. Lock order: registry lock, then room lock, never the reverse.

**Done when:** `python3 -m protocol_check.rooms_client --server <IP>` passes 40/40 against the real server, and new unit tests for `room.py` and `scoring.py` pass.

## Section 2 — Client core and terminal clients (days 3–5)

New package `fatrat/client/`.

| File | Job |
|---|---|
| `core.py` | `ClientSession`: TCP connect, reader thread, request/reply matching by `request_id`, latest STATE, UDP bind with `LiveStatusFilter`, local countdown estimate between snapshots. Reports everything to the interface through one callback; contains no interface code. |
| `storage.py` | Saves the host token and reconnect tokens per server address in a local JSON file. |
| `quizfile.py` | Load, save and validate quiz JSON files. Two sample quizzes in `quizzes/`. |
| `terminal_host.py`, `terminal_player.py` | Complete terminal interfaces. The PDF says a terminal interface is sufficient, so these alone satisfy requirement 8 and are the demo fallback. |
| `bot.py` | Simulated players and hosts (`--rooms 3 --players 5`), used for tests and to fill rooms in the demo. |

**Done when:** a full quiz is played from two terminals plus bots, first on one Mac, then with a client on a second device. This is the first moment the project is submittable.

## Section 3 — Robustness and host controls (days 5–7)

In this order, each with tests:

1. Disconnect handling: player disconnect keeps eligibility; host disconnect pauses an open question.
2. RESUME_SESSION with reconnect tokens, SESSION_REPLACED, client auto-reconnect with backoff.
3. PING/PONG heartbeat; client stall detection.
4. PAUSE_QUIZ, RESUME_QUIZ, CLOSE_ROOM, REMOVE_PLAYER with the REMOVED notice.
5. Replay rules: same-request SUBMIT_ANSWER replay, host result cache, `expected_control_rev` with STALE_STATE.
6. Client fallback: GET_STATE after three seconds of UDP silence.
7. Rate limit (10 requests per second) and SERVER_BUSY.

## Section 4 — Quiz editing during play (days 7–8, first thing to cut)

EDIT_BEGIN, EDIT_COMMIT, EDIT_CANCEL, EDIT_QUESTION, ADD_QUESTION, DELETE_QUESTION, REORDER_QUESTIONS, plus a quiz editor in the host client. If time runs out, editing stays lobby-only through PUT_QUIZ and the gap is stated in report §5. Nothing else depends on this section.

## Section 5 — Testing and evidence (starts day 2, finishes day 11)

- **Unit tests** in `tests/`: room handlers, scoring, projection, registry ownership. No sockets.
- **Game check** in a new `game_check/` folder: scripted clients against the real server covering the "Application tests still required" list in `docs/status-and-next-steps.md` (full gameplay and ties, the 4-versus-5 start gate, the 50 cap and the race for the last slot, late and duplicate answers, reconnect, removal, host disconnect, three rooms of five, a 50-player load run).
- **UDP impairment**: a server test flag that drops, duplicates and reorders LIVE_STATUS, to show the client filter working in the real game.
- **Planted bugs**: remove a lock or a check on purpose and confirm a test fails, as was done for the protocol layer.
- **Second device**: run the server on this Mac and clients on a teammate's laptop on the same Wi-Fi or a phone hotspot. Record how the IP was found (`ipconfig getifaddr en0`).
- **Wireshark**: `brew install --cask wireshark-app`, log out and in. Capture with filter `tcp.port == 43000 || udp.port == 43001`: the TCP handshake, one framed request with its 4-byte length, a STATE, the UDP_HELLO, a run of LIVE_STATUS datagrams, and a disconnect. Save `.pcapng` files and screenshots under `docs/test-results/`.
- Save raw outputs of every run under `docs/test-results/<date>/`.

## Section 6 — GUI (design from day 2 in parallel; build days 8–11)

**6a. Design in Claude Design (Abdallah, can start now).** Screens to design:

- *Host / shared display:* connect and host name; quiz library and editor; lobby with large PIN and player list; open question (text, 2–8 choices each with its cheese symbol, countdown, answered count); paused and editing states; results (correct answer, leaderboard, Mr Fat Rat fed by score); final podium; player management (remove); close-room confirmation.
- *Player:* join (server address, PIN, name); waiting in lobby; answer pad showing only the cheese symbols and countdown; answer locked in; own result (correct or wrong, points, total, rat growing); final rank; reconnecting, removed, left and room-full messages.
- *Assets:* eight clearly different cheese symbols, Mr Fat Rat at several sizes.

**6b. Build.** Recommended: **pywebview**. It opens a native window that renders the Claude Design HTML/CSS as-is; Python and the page talk through an in-process bridge, with no HTTP or WebSocket anywhere. The interface calls `ClientSession` methods (`join`, `submit_answer`, `next_question`…) and `ClientSession` pushes STATE and countdown updates to the page. New files: `fatrat/client/gui/app.py`, `bridge.py`, and `web/` holding the design.

If the instructor says the interface itself must be Python, rebuild the same design in **PySide6**, using the Claude Design screens as the specification. Because `core.py` has no interface code, this swap touches only `fatrat/client/gui/`.

Either way this adds one `pip install` for the GUI only; the server, client core, terminal clients and tests stay standard library.

**Rule:** no GUI build work starts until section 2 is done and section 3 items 1–4 pass.

## Section 7 — Documentation and code quality (continuous; final pass day 11)

- Every Python file starts with its purpose; important functions document inputs, outputs and the reason for socket, parsing, locking and error-handling choices. The PDF grades this.
- `README.md`: install, start the server, start host and player (terminal and GUI), find the server IP, firewall notes for macOS and Windows, run the tests.
- Keep `README.md`, `docs/status-and-next-steps.md`, `docs/protocol-test.md`, `AGENTS.md` = `CLAUDE.md`, and the vault notes in agreement after each section; append to the vault log.
- If any message or error code changes, update `catalogue.py` and the report tables together.

## Section 8 — Report §3–§6 (days 10–12)

Extend `docs/build_report.py`; keep the submitted §1–§2 text, correcting only what the implementation proved wrong.

- **§3 Design and Implementation:** architecture diagram, the thread table from section 1, shared resources and which lock guards each, lock order, timing and scoring policy, room and state management.
- **§4 Testing and Sample Runs:** screenshots of the main operations, the server IP step, three concurrent rooms, the Wireshark captures with explanation, results of concurrency and error tests, and the 50-player run with its hardware and network conditions.
- **§5 Challenges and Limitations:** real problems met, anything from sections 3–4 not finished, in-memory state, no encryption.
- **§6 References:** documentation used and the AI assistance disclosure, updated.
- Export the PDF, check every page, put it in `submitted_documents/`.

## Section 9 — Understanding the project and demo preparation (days 2–14)

- **Daily, 20 minutes:** after each section I write a short walkthrough in `docs/study/` of what was built that day: what each thread does, what each lock protects, one message traced from client to server and back. Abdallah reads it and asks questions the same day, not at the end.
- **`docs/study/demo-questions.md`:** likely examiner questions with answers that point to the exact file and line, grouped under the five topics the PDF names: protocol, TCP versus UDP, multithreading, synchronization, error handling.
- **Ownership:** once the team has talked, each member takes one area to explain in depth (server and locks; protocol and client; testing, UDP and Wireshark). Everyone still learns the one-page overview.
- **Demo script (about 10 minutes):** start the server and show its IP; three rooms of five with bots plus real GUI clients on two devices; play a question; show a wrong PIN, a duplicate name, a late answer and a duplicate answer; kill a player and reconnect; disconnect the host and resume; show LIVE_STATUS in Wireshark. Terminal clients ready as fallback.
- **Two rehearsals** on the real devices and network, days 12 and 13, with teammates asking each other the question list.

## Schedule

| Days | Dates | Work |
|---|---|---|
| 1–4 | 16–19 Nov | Sections 0 and 1; GUI design starts |
| 3–5 | 18–20 Nov | Section 2; first second-device run |
| 5–7 | 20–22 Nov | Section 3 |
| 7–8 | 22–23 Nov | Section 4 (cut if late) |
| 8–11 | 23–26 Nov | Section 6b; section 5 finished, Wireshark |
| 10–12 | 25–27 Nov | Section 8 |
| 12–14 | 27–29 Nov | Rehearsals, fixes, final PDF, submission package |

Cut order if time runs short: section 4, then rate limiting, then GUI polish. Never cut: sections 1, 2, 5, 8, 9.

## Verification

- `python3 -m unittest -v`: all tests pass, none skipped.
- `python3 -m fatrat.server`, then `python3 -m protocol_check.rooms_client --server <IP>`: 40/40 against the real server.
- `python3 -m game_check ...`: every scenario passes, including three rooms of five and the 50-player run.
- A full quiz played by hand through the GUI with the server on this Mac and at least one client on another device.
- Wireshark capture showing framed TCP messages and LIVE_STATUS datagrams.
- Final PDF contains sections 1–6 and matches `catalogue.py` (consistency test passes).
- Each team member answers the question list without notes.
