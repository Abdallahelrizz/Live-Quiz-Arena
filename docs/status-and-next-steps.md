# Fat-Rat status and remaining work

Updated: 2026-11-17 by Claude Code.

**Sections 1 and 2 are submitted. The game server and terminal clients are built, implement every message of section 2, and pass their tests on one Mac (2026-11-17).** Not yet done: a client on a second device, Wireshark captures and screenshots, the graphical interface, putting sections 3 to 6 into the report (a draft exists: [report-sections-3-to-6-draft.md](report-sections-3-to-6-draft.md)), demo rehearsals. The whole plan, with dates, is in [project-plan.md](project-plan.md). Final submission and demo are expected around 2026-11-30 (Abdallah, 2026-11-16; exact date to confirm with the instructor).

## Game build (2026-11-16 and 17)

Built on `fatrat/protocol/`, standard library only. How it works: [study/01-how-fat-rat-works.md](study/01-how-fat-rat-works.md).

- [x] Server `fatrat/server/`: accept loop, reader and writer thread per connection, registry (PINs, one unfinished room per host, creation ledger), room engine with one lock per room, per-recipient STATE, scoring and leaderboard, one timer thread per room, UDP registration and LIVE_STATUS.
- [x] Server messages working: CREATE_ROOM, JOIN_ROOM, RESUME_SESSION, PUT_QUIZ, START_QUIZ, SUBMIT_ANSWER, PAUSE_QUIZ, RESUME_QUIZ, CLOSE_QUESTION, NEXT_QUESTION, CLOSE_ROOM, REMOVE_PLAYER, LEAVE_ROOM, GET_STATE, REQUEST_UDP_BIND, UDP_HELLO, UDP_BOUND, LIVE_STATUS, REMOVED, SESSION_REPLACED, RESULT, ERROR, STATE.
- [x] Disconnects: a player keeps identity, slot and score; a host drop pauses an open question; token reconnect; a second connection replaces the first; repeated requests are not run twice; stale host commands get STALE_STATE.
- [x] Client `fatrat/client/`: `core.py` (all networking, automatic reconnect, UDP filter, local countdown, GET_STATE after UDP silence), saved host identity and reconnect tokens, quiz files, terminal host, terminal player, bots.
- [x] Tests on one Mac, loopback: 118 automated tests pass (57 protocol, 39 room engine with a fake clock, 14 over real sockets, 8 for the client's files). Latest output: `docs/test-results/2026-11-17/unit-tests.txt`. The earlier rooms check passes 40/40 against the real server. Bots ran 3 rooms of 5 and one room of 50 to the end. A full quiz was played through the terminal host and player. Outputs: `docs/test-results/2026-11-16/`.
- [x] PING/PONG heartbeat: a client silent for 15 s after a PING is dropped and handled as a normal disconnect (tested).
- [x] Rate limit: 10 requests per second per connection with a burst of 20, then RATE_LIMITED with retry_after_ms; the client waits and retries once (tested). SERVER_BUSY at 500 rooms. Note: the report says 10 per second; the burst of 20 is an implementation detail to mention in section 3.
- [x] Size budget: joins and quiz changes that could make the host STATE exceed one frame are refused (tested with a lowered limit).
- [x] Editing: EDIT_BEGIN, EDIT_COMMIT, EDIT_CANCEL, EDIT_QUESTION, ADD_QUESTION, DELETE_QUESTION, REORDER_QUESTIONS on the server (tested). The terminal host can use all of them: `edit` (the question on screen), `edit <n>`, `move <n> <m>`, `append <file>`, `delete <n>`, `questions` (tested over real sockets, 2026-11-17).
- [x] Lost CREATE_ROOM reply: the host client saves the request ID before sending and resends the same request; the server answers ROOM_ALREADY_CREATED. Tested on both sides, including a real lost reply over sockets (2026-11-17).
- [x] `--impair-udp` server test mode: drops, duplicates and reorders LIVE_STATUS; the client rejects the bad ones and the game is unaffected (tested).
- [x] UDP silence: after 3 s without a snapshot the client refreshes once over TCP with GET_STATE and marks the countdown approximate; the question still closes by TCP (tested 2026-11-17).
- [x] Concurrency test proven to bite: with the registry lock removed on purpose, the simultaneous-create test fails (8 rooms instead of 1); restored afterwards. `docs/test-results/2026-11-17/planted-bug.txt`.
- [x] Draft of report sections 3, 5 and 6, and of section 4 with the results that exist: [report-sections-3-to-6-draft.md](report-sections-3-to-6-draft.md).
- [ ] Graphical interface: designed in Claude by Abdallah from [gui-design-brief.md](gui-design-brief.md), then built on `core.py`.
- [ ] Second-device run, Wireshark captures, screenshots: need a person, a second device and Wireshark installed.

The sections below are kept from 5 November. The protocol test section describes the earlier test server. The two original lists further down were reconciled with the tests on 2026-11-17.

## Protocol test (built 2026-11-05)

Details, commands and the full check list are in [protocol-test.md](protocol-test.md).

- [x] Built the protocol layer in `fatrat/protocol/` (framing, catalogue, validation, UDP filter), meant to be reused by the real server and clients.
- [x] 57 automated tests pass, including a check that the Word report's message and error tables match the code.
- [x] Protocol check passes on this Mac: 36/36 with one player, 151/151 with five at once. It covers framing, invalid messages, UDP loss, duplication and reordering, answers, and fatal input.
- [x] Rooms check passes on this Mac: 40/40. It covers the largest quiz, the 50-player maximum, the 51st player rejected, the last-slot race, the start gate, and three rooms at once with 5 players each.
- [x] Planted bugs (7 in the protocol code, 4 in the test server) were all caught by the tests.
- [ ] Run the protocol and rooms checks with the client on a **second device**.
- [ ] Capture the TCP and UDP traffic in Wireshark; screenshots are listed in protocol-test.md. Deferred to Section 4 (Abdallah, 2026-11-05). Install with `brew install --cask wireshark-app`.
- [x] Add the protocol results to section 2.13 at Abdallah's request; second-device and Wireshark work remains deferred to Section 4.
- [x] Independently rerun the existing suite with Codex: 57 unit tests and 227 network assertions passed on one Mac over loopback; save raw outputs under docs/test-results/2026-11-05-codex/.

These checks ran against `protocol_check/server.py`, a test server. They show that the covered scenarios pass on the test server; unexercised protocol behavior is not established. They are not results for the future game server, so the application tests below stay unchecked until that server exists.

## Completed design and document checks

- [x] Documented the Fat-Rat name, 5–50 player policy excluding the host, 2–8 choices, 1–30 questions, cheese symbols and score-based mascot presentation.
- [x] Integrated saved host identity, one unfinished room per host, successive rooms after finish/close, reconnect behavior, creation recovery and CLOSE_ROOM into the protocol and report.
- [x] Recorded six completed Claude reviews and Codex's assessment/corrections in [protocol-review.md](protocol-review.md), with raw responses in [reviews](reviews/).
- [x] Checked scoring calculations and equivalent integer rounding at representative time limits and remaining times. This was arithmetic validation, not a running-server scoring test.
- [x] Checked the 32 unique message types and document structure for the revised protocol fields and stale pending-policy text.
- [x] Generated the Word report with formal logo/team opening and section 2.13, rendered it, and visually inspected the final pages, using exact image comparisons to preserve inspection of unchanged pages after small corrections.
- [x] Verified black text, black table borders, white backgrounds, monochrome embedded diagrams and two editable native Word equations.

The current artifact is [the sections 1 and 2 report](../output/docx/CSC430_Fat_Rat_Sections_1_and_2.docx). Local document-QA evidence is in `tmp/report-qa/verification.txt`; its final render is `tmp/report-qa/protocol-validation-final/`. Rendered QA PDFs and page images are internal checks, not the submission PDF.

## Original implementation list (from 5 November), reconciled on 2026-11-17

- [x] Build the Python raw-socket TCP/UDP server and threaded room/session handling. `fatrat/server/`.
- [x] Build the host and player clients, saved identity/reconnect handling, quiz save/load/editing and all agreed gameplay. `fatrat/client/`, terminal interfaces.
- [ ] Choose the interface toolkit, produce eight distinguishable cheese icons and implement the Mr Fat Rat score presentation. This is the graphical interface, not built yet. Artwork, feeding scale and animation are interface details, not a second scoring system.
- [x] Add meaningful automated tests and test clients for the implemented behavior. `tests/`, `fatrat/client/bot.py`, `protocol_check/rooms_client.py`.
- [x] Document installation, dependencies, server/client commands, LAN setup and important code in the README/source.

## Original application test list (from 5 November), reconciled on 2026-11-17

Everything ticked here ran **on one Mac over loopback**. Test names are in `tests/`; `rooms check` is `protocol_check/rooms_client.py` run against the game server.

- [x] Full gameplay: joins, answers, scoring, grouped ties, final rankings and host advancement. `test_scores_use_server_time...`, `test_leaderboard_dense_ranks_and_ties`, `test_next_question_then_finish...`, `test_three_rooms_of_five...`, bots, a full terminal game.
- [x] Boundaries: 4 versus 5 connected players at start (`test_start_needs...`); the 50-player cap counting disconnected players and the 51st rejected (`test_fifty_player_cap_and_slot_reuse`); concurrent joins for the last slot (rooms check C13); resume at capacity (`test_resume_in_a_full_room_uses_the_kept_slot`); leave and removal freeing a slot while keeping history (`test_leave_revokes...`, `test_remove_player`).
- [x] Quiz boundaries: 1/30/31 questions, 1/2/8/9 choices, timer limits and invalid content (`tests/test_messages.py`, `test_question_limits_and_frozen_history`); the outgoing-size budget, tested with a lowered limit (`test_room_refuses_growth...`).
- [x] Duplicate, invalid and late answers; TCP split and merged reads (`tests/test_framing.py`); malformed messages (`test_bad_bytes...`); pause, timeout and close races (`test_late_answer...`, `test_close_room_at_a_passed_deadline...`, `test_close_room_racing_the_final_next_question`); current-question restarts and future edits versus advancement (`EditingTests`).
- [x] Player and host disconnection and reconnect, token persistence (`ProfileTests`), revoked tokens, lost replies (`test_lost_create_reply...`, `test_lost_close_reply...`), session replacement and stale-connection cleanup (`test_second_connection_replaces_the_first`).
- [x] Host ownership: concurrent creation, lost creation reply, repeated creation after finish, closing while editing, closing versus deadline, lost closing reply, late action on an old room. The `RoomCreationAndJoinTests`, `HostControlTests` and `EditingTests` added on 2026-11-17.
- [x] UDP loss, duplication, delay and reordering (`--impair-udp`, `test_lost_duplicated_and_reordered_udp...`); sequence and revision filtering (`tests/test_udp.py`); recovery when UDP goes silent (`test_udp_silence_triggers_a_tcp_refresh...`); rebinding after reconnect (`test_reconnect_drops_the_old_binding...`). Scores and phase never depend on UDP.
- [ ] 50-player load test **across real devices**, with the hardware and network recorded. Done so far: one room of 50 simulated players on one Mac (`docs/test-results/2026-11-16/`).
- [ ] Three simultaneous rooms, each with its own host and at least five players, **with at least one client on another physical device**. Done so far: three rooms of five on one Mac.
- [ ] Capture the TCP and UDP traffic in Wireshark and keep the screenshots.

## Report and submission work still required

- [x] Add Abdallah Elrizz (202301345), Rayan Madi (202401943) and Tala Daouk (202304053), with the supplied institutional logo.
- [ ] Apply any page-length guidance subsequently supplied by the instructor; the provided requirements specify no page/word limit.
- [x] Sections 1 and 2 submitted as `submitted_documents/CSC430_Fat_Rat_Sections_1_and_2.pdf`. The final report PDF (sections 1 to 6) is still to export.
- [ ] After implementation, write Section 3 Design and Implementation.
- [ ] Record the functional, capacity, LAN and Wireshark evidence in Section 4 Testing and Sample Runs, including the instructor's requested proof that it works.
- [ ] Write Section 5 Challenges and Limitations from actual implementation/testing experience.
- [ ] Update Section 6 References and AI assistance disclosure as the full project develops; references already accompany the sections 1 and 2 report.

Next steps, in order: second-device run, Wireshark captures and screenshots, the interface, then moving the section 3 to 6 draft into the report. See [project-plan.md](project-plan.md).
