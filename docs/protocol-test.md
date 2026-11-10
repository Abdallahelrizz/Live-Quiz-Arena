# Fat-Rat protocol test

Updated: 2026-11-05 by Codex; original protocol implementation and checks by Claude Code.

**What this is:** a test of the application-layer protocol from report section 2, built before the game itself. The game server and clients are still not built. This tests the protocol: framing, message formats, error handling, UDP behaviour, room limits and several rooms at once.

## Results so far

| Run | Where | Result |
|---|---|---|
| Automated tests (`python3 -m unittest -v`) | this Mac | 57/57 passed |
| Protocol check, 1 player | this Mac, loopback and its own LAN address | 36/36 passed |
| Protocol check, 5 players at once | this Mac, its own LAN address | 151/151 passed |
| Rooms check (largest quiz, 50-player maximum, 3 rooms at once) | this Mac, its own LAN address | 40/40 passed |
| **Client on a second device** | not run yet | required by the project; see below |
| **Wireshark captures** | not run yet; deferred to Section 4 (Abdallah, 2026-11-05) | Wireshark is not installed; `brew install --cask wireshark-app` |

Server and client both ran on the same Mac. The requirement of at least one client on a different device has not been demonstrated yet.

To check that the tests really detect mistakes, bugs were planted on purpose. All 11 were caught:
- In the protocol code: reading past the end of a TCP frame, accepting duplicate or older UDP sequence numbers, allowing unknown fields, allowing duplicate JSON keys, counting booleans as integers, and moving LIVE_STATUS to TCP.
- In the test server: a 51-player cap, a 4-player start gate, no early close when everyone has answered, and duplicate names allowed.

## Independent rerun and report update 2026-11-05

Codex independently reran the existing suite on the same Mac over loopback IPv4 127.0.0.1 (TCP 43100, UDP 43101): **57/57 unit tests, 36/36 single-client assertions, 151/151 five-client assertions and 40/40 room assertions passed**. The 227 network assertions include repeated checks; they are not 227 unique scenarios. The 10-second stalled-frame test ran in the one-client suite and was skipped in the five-client suite. Raw outputs, exact commands and summary are in [test-results/2026-11-05-codex](test-results/2026-11-05-codex/README.md).

The Word report now contains **section 2.13 Protocol validation**, the supplied LAU School of Arts and Sciences logo, and the three students' names/IDs. The report remains black and white with native editable equations. These results demonstrate the covered protocol/test-server scenarios only; they do not show that the full protocol state machine or future game works. No full game implementation, second-device result or Wireshark capture is claimed. The test server was stopped after the rerun.

## What was built

| Path | What it is |
|---|---|
| `fatrat/protocol/framing.py` | TCP framing: 4-byte big-endian length + UTF-8 JSON, exact-read loop, 1 MiB limit, 10 s partial-frame timeout, strict JSON (no duplicate keys, NaN or non-objects). |
| `fatrat/protocol/catalogue.py` | The 32 message types (sender, receiver, transport, fields) and 32 error codes, as data. |
| `fatrat/protocol/messages.py` | Validation of envelopes, data fields, RESULT per request, ERROR rules, STATE snapshot rules and quiz limits. |
| `fatrat/protocol/udp.py` | 512-byte datagrams and the LIVE_STATUS filter (source, binding, room/question/attempt/revision, strictly increasing seq). |
| `tests/` | Automated tests. `test_report_consistency.py` reads the tables out of the Word report and fails if they disagree with the code. |
| `protocol_check/server.py` | **Test server, not the game server.** Speaks the protocol, runs a scripted one-question session per player, and deliberately loses, duplicates, reorders and corrupts UDP datagrams. Also hosts real rooms (`rooms.py`). |
| `protocol_check/client.py` | Protocol check: framing, invalid messages, UDP and answer checks (T/V/U/A/F). |
| `protocol_check/rooms_client.py` | Rooms check: largest quiz, 50-player maximum, 3 rooms at once (L/C/R). |

`fatrat/protocol/` is meant to become the protocol layer of the real server and clients. The test server is test scaffolding; it does not implement pause, editing, removal, reconnect or the heartbeat.

The project allows "testing scripts to simulate multiple players and rooms", which is what the simulated hosts and players in the checks are.

## How to run it on two devices

Requirements: Python 3.8 or newer on both devices, no extra packages, both devices on the same Wi-Fi. Both need this project folder (OneDrive sync or a copy). Run every command from the project folder. On Windows, use `py` or `python` instead of `python3`.

1. **Server device:** `python3 -m protocol_check.server`
   It prints its IPv4 address, the test room PIN and the exact client commands. If macOS asks whether Python may accept incoming connections, choose **Allow**.
   To find the IP yourself (report section 4 asks you to show this): macOS `ipconfig getifaddr en0`, Windows `ipconfig` (the "IPv4 Address" line).
2. **Client device:**
   - `python3 -m protocol_check.client --server <IP> --pin <PIN>` (about 25 s)
   - `python3 -m protocol_check.rooms_client --server <IP>` (about 20 s; opens about 75 connections)
   - optional: `python3 -m protocol_check.client --server <IP> --pin <PIN> --players 5 --skip-slow`
3. Each client prints PASS/FAIL per check and exits with code 0 only if everything passed. The server terminal logs every connection, request and outcome, with the client's IP address.

If a UDP check (U3–U6) fails on Wi-Fi, look at the printed datagram table first. Real Wi-Fi can lose one of the deliberately impaired test datagrams; run it again. U5b passes whether or not a firewall blocks the wrong-port datagram, because what it checks is that nothing from another address is ever accepted.

## Wireshark captures for the report

Capture on the Wi-Fi interface of either device with the display filter `tcp.port == 43000 || udp.port == 43001`. Useful screenshots:

- **TCP framing:** any TCP segment's payload starts with 4 length bytes (for example `00 00 01 2b` = 299) followed by `{"v":1,...`. Use Follow → TCP Stream.
- **T1:** the JOIN_ROOM sent one byte per TCP segment (about 130 segments with 1-byte payloads).
- **T3:** three requests in a single TCP segment.
- **UDP:** UDP_HELLO from the client, then LIVE_STATUS datagrams from port 43001. Decode As → JSON shows the fields. Also visible: the duplicate datagram, the late seq 8, the datagram over 512 bytes and the one from a different port.
- **F4/F5:** the server closing the connection (FIN) right after a bad length header, with no reply.
- **L1:** one 428 KB PUT_QUIZ split across many TCP segments and reassembled into one message.

## What each check tests

Report section 2 requirement → checks:

| Requirement (report section 2) | Automated tests | Network checks |
|---|---|---|
| Message boundaries: partial reads and several messages per read | `test_framing` (every split position, byte by byte, coalesced, 1.5 frames) | T1, T2, T3, L1 |
| Length limits, EOF, stalled frames | `test_framing` (0, 1 MiB, 1 MiB+1, EOF mid-frame, partial timeout) | F4, F5, F6 |
| Strict JSON: invalid UTF-8/JSON, duplicate keys, NaN | `StrictJsonTest` | F1, F2 |
| Every message type's fields, direction and transport | `test_messages` (all 24 client types, each field missing or wrong) | V1–V4, M1, M2 |
| Version handling | `test_versions` | F3 |
| Error codes and their extra fields | `test_error_rules`, `test_result_data_per_request` | V5, V6, A2, A4, A6, A8, C2–C13, L2–L6 |
| Report tables match the code | `test_report_consistency` | — |
| UDP size limit (512 bytes, worst case 464) | `DatagramSizeTest` | U8 |
| UDP loss, duplication, reordering; stale or foreign datagrams | `LiveStatusFilterTest`, `RealSocketImpairmentTest` | U2–U7, U5b |
| UDP endpoint registration | — | U1, R5 |
| Answers: invalid, duplicate, replay, stale, late; scoring | — | A1–A8, C15, C17 |
| Players never see question text or the correct answer early | `test_state_invariants` | A1, A3, M1 |
| Quiz limits: 1–30 questions, 2–8 choices, 1–300 s, text lengths | `QuizRulesTest` | C4, L1–L5 |
| 50-player maximum, 51st rejected, disconnected players keep their slot, leave frees it | — | C7–C13, C16 |
| Concurrency: simultaneous joins, last-slot race, simultaneous answers | — | C7, C13, C15, R3 |
| 5-player start gate, one unfinished room per host | — | C2, C6, C21 |
| Three rooms at once with independent players, timers, live counts and scores | — | R1–R12 |
| The server survives bad clients | — | F1–F7 |

## Not covered yet

These parts of section 2 are not tested because they need the real server:
- pause/resume and editing
- explicit CLOSE_ROOM and its lost-response recovery
- host removal of players
- RESUME_SESSION and reconnect tokens
- PING/PONG heartbeat
- rate limiting
- the GET_STATE fallback after 3 s without UDP
- host-disconnect pause (implemented in the test server but not tested)
- SESSION_REPLACED

Capacity was measured on one Mac only. A 50-player run across real devices and Wireshark evidence are still to do.
