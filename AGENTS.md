# Fat-Rat: project memory for AI agents

**Last updated: 2026-11-05 15:41 EET (UTC+2) by Claude Code**

`AGENTS.md` (read by Codex) and `CLAUDE.md` (read by Claude Code) are identical copies. When you change one, copy it over the other and update the timestamp line in both. Check with `cmp AGENTS.md CLAUDE.md`.

## Read first

1. Shared memory rules: `~/Documents/Vault/RULES.md`. Read them at the start of every task. After work, update the vault notes and append a line to `~/Documents/Vault/log.md`.
2. Vault entry note: `~/Documents/Vault/LAUprojects/csc430-computer-networks/_overview.md`. Protocol test note: `LAUprojects/csc430-computer-networks/testing/csc430-protocol-test.md`.
3. Repository: `README.md` (entry point), `docs/status-and-next-steps.md` (checklist), `docs/protocol-test.md` (test commands and results).

## The project

CSC430 Computer Networks, LAU, Fall 2026. Team of three: Abdallah and two teammates, whose names are not recorded yet. **Fat-Rat** is a multiplayer, Kahoot-style quiz in Python using raw `socket` TCP and UDP plus `threading`. No frameworks. Requirements: `source_documents/Project Requirements.pdf`.

The requirements PDF does **not** define phases or dates. It defines three deliverables:
1. Source code and README.
2. A technical report PDF with sections 1–6.
3. A live demo, worth 50% and graded individually.

The deadline of **Friday 2026-11-06 for report sections 1–2** comes from Abdallah, not from the PDF.

## Current status (2026-11-05 15:41)

| Part | Status |
|---|---|
| Report §1 Application Description | Written (Word report, 16 pages, Codex). Not changed on 2026-11-05 by Claude Code. |
| Report §2 Application-Layer Protocol | Written; 32 message types, 32 error codes. **Tested on 2026-11-05: it works as written** (see below). |
| Report §3 Design and Implementation | Not written. Some of its material now exists: the protocol layer, the threads and locks of the test server. |
| Report §4 Testing and Sample Runs | Not written. Protocol test results exist; the two-device run and Wireshark captures are still missing. |
| Report §5 Challenges, §6 References | Not written. |
| Protocol code (`fatrat/protocol/`) | **Built and tested.** Meant to be reused by the real game. |
| Game application (real server, host and player clients, interface) | **Not built.** |
| Before submitting §1–§2 | Add team names and student IDs; export and check the PDF. |

## Timeline

All times EET (UTC+2). Times before 2026-11-05 02:00 come from git, file times and the vault log.

| When | Who | What |
|---|---|---|
| 2026-11-03 11:57 | Abdallah | Initial commit: course PDFs in `source_documents/`. |
| 2026-11-03 | Claude Desktop | Vault notes made from the three course PDFs. |
| 2026-11-04 | Codex | Gameplay decisions, protocol specification (`docs/section-2-protocol.md`), Word report for §1–§2, reciprocal Claude reviews. Team renamed the game Fat-Rat; 5–50 players, 2–8 choices, 1–30 questions. |
| 2026-11-05 00:56–01:28 | Codex | Protocol v7 (saved host identity, one unfinished room per host, CLOSE_ROOM); final Word report (01:08); README and status checklist; git commit at 01:28. |
| 2026-11-05 ~02:00 | Claude Code | Abdallah said the instructor asked for a test. Requirements PDF re-checked: it has no phases. Agreed with Abdallah that the test means **testing the §2 protocol**. Started `fatrat/protocol/framing.py` (02:14). |
| 2026-11-05 11:42–11:46 | Claude Code | Built the protocol layer (catalogue, validation, UDP filter) and 57 automated tests; all pass. 7 planted bugs, all caught. |
| 2026-11-05 11:52–11:53 | Claude Code | First protocol check run: 35/35 on loopback, then 147/147 with 5 players over the Mac's LAN address. |
| 2026-11-05 ~12:00–12:50 | Claude Code | Abdallah asked to also check the maximum and multiple rooms. Added real rooms to the test server (`rooms.py`) and `rooms_client.py`: largest quiz, 50-player maximum, 3 rooms at once. 4 planted server bugs, all caught. |
| 2026-11-05 12:50–12:52 | Claude Code | Final run, all passing: 57 unit tests, protocol check 36/36 and 151/151 (5 players), rooms check 40/40. Wrote `docs/protocol-test.md`, updated README, status checklist and vault. |
| 2026-11-05 13:03 | Claude Code | Wrote this file (AGENTS.md = CLAUDE.md) and the vault note `csc430-protocol-test`. Abdallah will edit the report himself to show the test. |
| 2026-11-05 15:41 | Abdallah + Claude Code | **Decision:** Wireshark captures and the second-device run wait for Section 4. They are not needed for the §1–§2 submission, and teammates have no time now; one person can do them later. Section 3 will be written after the real game is built. |

## Repository layout

| Path | Contents |
|---|---|
| `source_documents/` | Course PDFs: requirements, Wireshark getting started, HTTP Wireshark sample lab. |
| `docs/section-2-protocol.md` | Detailed protocol specification (source of the report's §2). |
| `docs/gameplay-decisions.md`, `docs/protocol-review.md`, `docs/reviews/` | Agreed gameplay, review findings, raw reviews. |
| `docs/build_report.py`, `output/docx/CSC430_Fat_Rat_Sections_1_and_2.docx` | Report builder and the §1–§2 Word report. |
| `docs/status-and-next-steps.md` | Checklist of done and remaining work. |
| `docs/protocol-test.md` | Protocol test: commands, Wireshark steps, check list, results. |
| `fatrat/protocol/framing.py` | TCP framing: `uint32_be(N)` + UTF-8 JSON, exact-read loop, 1 MiB limit, 10 s partial-frame timeout, strict JSON. |
| `fatrat/protocol/catalogue.py` | The 32 message types (sender, receiver, transport, fields), 32 error codes, limits. |
| `fatrat/protocol/messages.py` | Validation: envelope, data fields, RESULT per request, ERROR rules, STATE rules, quiz limits. |
| `fatrat/protocol/udp.py` | 512-byte datagrams; LIVE_STATUS filter (source, binding, IDs, control_rev, strictly increasing seq). |
| `tests/` | 57 automated tests. `test_report_consistency.py` reads the Word report's tables and fails if they differ from `catalogue.py`. |
| `protocol_check/server.py` | **Test server, not the game server.** Scripted one-question session per player with deliberate UDP loss, duplication, reordering and corruption; also hosts real rooms. |
| `protocol_check/rooms.py` | Room engine of the test server: CREATE_ROOM, JOIN_ROOM (50 cap), PUT_QUIZ, START_QUIZ (5-player gate), answers, scoring, NEXT_QUESTION, LEAVE_ROOM, LIVE_STATUS. No pause, editing, removal, reconnect or heartbeat. |
| `protocol_check/client.py` | Protocol check (T/V/U/A/F checks). |
| `protocol_check/rooms_client.py` | Rooms check (L/C/R checks). |
| `tmp/report-qa/` | Codex's internal render checks of the report; not deliverables. |

## How to run

Python 3.8+, standard library only (this Mac has 3.9.6). Run from the repository folder.

```bash
python3 -m unittest -v                                          # 57 automated tests, about 2 s
python3 -m protocol_check.server                                # prints its IPv4 address and a test-room PIN
python3 -m protocol_check.client --server <IP> --pin <PIN>      # protocol check, about 25 s
python3 -m protocol_check.client --server <IP> --pin <PIN> --players 5 --skip-slow
python3 -m protocol_check.rooms_client --server <IP>            # largest quiz, 50-player maximum, 3 rooms, about 20 s
```

Ports: TCP 43000, UDP 43001. Wireshark filter: `tcp.port == 43000 || udp.port == 43001`.

## Test results (2026-11-05, server and client on one Mac)

| Run | Result |
|---|---|
| Automated tests | 57/57 |
| Protocol check, 1 player | 36/36 |
| Protocol check, 5 players at once | 151/151 |
| Rooms check | 40/40 |
| Planted bugs caught | 11/11 (7 in protocol code, 4 in the test server) |

Numbers worth quoting:
- The worst-case LIVE_STATUS datagram is 464 bytes (limit 512).
- The largest allowed quiz is a 428,179-byte PUT_QUIZ frame, accepted and echoed back intact.
- 46 players joined at the same time in 0.09 s.
- With a full room, 49 connected players received STATE OPEN within about 20 ms, and their 49 simultaneous answers were accepted in 5 ms.
- In a race of 3 joins for 1 free slot, exactly 1 was accepted.
- Across 3 rooms at once, room 1 timed out at its own 6 s while rooms 2–3 closed as soon as everyone answered.

**Not yet done:**
- A client on a second device (the project requires this).
- Wireshark captures (deferred to Section 4 by Abdallah on 2026-11-05). Wireshark is not installed on this Mac; install with `brew install --cask wireshark-app`, which gives the GUI app plus ChmodBPF for capture permission. Do not use the plain `wireshark` formula, which is command-line only. Log out and in after installing.
- A 50-player run across real devices.

**Not covered by the test:** pause/resume, editing, removal, RESUME_SESSION and reconnect tokens, PING/PONG, rate limiting, the GET_STATE fallback after UDP silence, SESSION_REPLACED.

## Showing the test in the report

Abdallah edits the Word report himself; agents do not change it unless he asks. The requirements put testing in §4 ("describe the results of your concurrency, error-handling, and other tests", with screenshots and Wireshark). Options:
- add a short "Protocol validation" subsection at the end of §2 now, or
- keep it for §4.

Ask the instructor which he wants.

Material to include: the results table above, the numbers above, and screenshots of the PASS output, the server log, the server IP (`ipconfig getifaddr en0` on macOS, `ipconfig` on Windows) and Wireshark (see `docs/protocol-test.md`).

Draft text to edit:

> **Protocol validation.** Before implementing the game, the protocol in this section was implemented as a reusable Python module (TCP framing, the message catalogue and validation, and the UDP snapshot filter) and tested. 57 automated tests check the following:
> - framing: partial and coalesced reads, length limits and stalled frames
> - every message type's fields and transport, the error codes and the quiz limits
> - UDP filtering
>
> One test reads this report's message and error tables and confirms that they match the code. A scripted test server and simulated clients then exercised the protocol over real TCP and UDP sockets, and all 227 checks passed. The scenarios included:
> - deliberate UDP loss, duplication and reordering
> - malformed and oversized input
> - a 50-player room with the 51st player rejected and a race for the last slot
> - three concurrent rooms with five players each and separate timers and scores
>
> These runs used one computer; the run with a client on a second device and the Wireshark captures are reported in Section 4.

Update the last sentence once the two-device run and the captures exist.

## Rules for agents in this repository

- Report status honestly:
  - The test server is a test peer, not the game. Never say the game works.
  - Do not mark plans as results.
  - Keep `README.md`, `docs/status-and-next-steps.md`, `docs/protocol-test.md`, the vault notes and this file in agreement.
- If you change the report's message or error tables, update `fatrat/protocol/catalogue.py` too, and the reverse. `tests/test_report_consistency.py` enforces this.
- Build the real server and clients on `fatrat/protocol/`. Use the standard library only. Document every file and important function; the requirements grade this.
- Do not edit the Word report unless Abdallah asks. Commit only when asked. Nothing from 2026-11-05 after 01:28 is committed yet.
- Next build step: the real server, host and player clients on top of `fatrat/protocol/`, then the remaining controls and the interface.
