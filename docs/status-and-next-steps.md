# Fat-Rat status and remaining work

Updated: 2026-11-05 by Claude Code.

**The written design for sections 1 and 2 is complete with the agreed changes.** On 2026-11-05 the instructor asked for a test. The section 2 protocol was then built and tested, on one Mac only; see the next section. The quiz game itself (real server, clients, interface) has not been built. "Report complete" means the design is documented; it does not mean the game works or that the project is finished.

## Protocol test (built 2026-11-05)

Details, commands and the full check list are in [protocol-test.md](protocol-test.md).

- [x] Built the protocol layer in `fatrat/protocol/` (framing, catalogue, validation, UDP filter), meant to be reused by the real server and clients.
- [x] 57 automated tests pass, including a check that the Word report's message and error tables match the code.
- [x] Protocol check passes on this Mac: 36/36 with one player, 151/151 with five at once. It covers framing, invalid messages, UDP loss, duplication and reordering, answers, and fatal input.
- [x] Rooms check passes on this Mac: 40/40. It covers the largest quiz, the 50-player maximum, the 51st player rejected, the last-slot race, the start gate, and three rooms at once with 5 players each.
- [x] Planted bugs (7 in the protocol code, 4 in the test server) were all caught by the tests.
- [ ] Run the protocol and rooms checks with the client on a **second device**.
- [ ] Capture the TCP and UDP traffic in Wireshark; screenshots are listed in protocol-test.md. Deferred to Section 4 (Abdallah, 2026-11-05). Install with `brew install --cask wireshark-app`.
- [ ] Decide with the instructor whether these results go in the report now or in Section 4.

These checks ran against `protocol_check/server.py`, a test server. They show that the protocol works. They are not results for the future game server, so the application tests below stay unchecked until that server exists.

## Completed design and document checks

- [x] Documented the Fat-Rat name, 5–50 player policy excluding the host, 2–8 choices, 1–30 questions, cheese symbols and score-based mascot presentation.
- [x] Integrated saved host identity, one unfinished room per host, successive rooms after finish/close, reconnect behavior, creation recovery and CLOSE_ROOM into the protocol and report.
- [x] Recorded six completed Claude reviews and Codex's assessment/corrections in [protocol-review.md](protocol-review.md), with raw responses in [reviews](reviews/).
- [x] Checked scoring calculations and equivalent integer rounding at representative time limits and remaining times. This was arithmetic validation, not a running-server scoring test.
- [x] Checked the 32 unique message types and document structure for the revised protocol fields and stale pending-policy text.
- [x] Generated the 16-page Word report, rendered it, and visually inspected every final page, using exact image comparisons to preserve inspection of unchanged pages after small corrections.
- [x] Verified black text, black table borders, white backgrounds, monochrome embedded diagrams and two editable native Word equations.

The current artifact is [the sections 1 and 2 report](../output/docx/CSC430_Fat_Rat_Sections_1_and_2.docx). Local document-QA evidence is in `tmp/report-qa/verification.txt`; its final render is `tmp/report-qa/complete-black-verified/`. Rendered QA PDFs and page images are internal checks, not the submission PDF.

## Implementation still required

- [ ] Build the Python raw-socket TCP/UDP server and threaded room/session handling.
- [ ] Build the host and player clients, saved identity/reconnect handling, quiz save/load/editing and all agreed gameplay.
- [ ] Choose the interface toolkit, produce eight distinguishable cheese icons and implement the Mr Fat Rat score presentation. Artwork, feeding scale and animation are interface details, not a second scoring system.
- [ ] Add meaningful automated tests and test clients for the implemented behavior.
- [ ] Document installation, dependencies, server/client commands, LAN setup and important code in the README/source.

## Application tests still required (none run on the real server yet)

- [ ] Exercise full gameplay: joins, answers, scoring, grouped ties, final rankings and host advancement.
- [ ] Check boundaries: 4 versus 5 connected players at start; the 50-player cap including disconnected ACTIVE players; 51st-player rejection; concurrent joins at 49; resume at capacity; leave/removal freeing a slot while preserving history.
- [ ] Check quiz boundaries: 1/30/31 questions, 1/2/8/9 choices, timer limits, invalid quiz content and outgoing-state size budgets, including extensive retained departure history.
- [ ] Test duplicate/invalid/late submissions, TCP fragmentation/coalescing, malformed messages, pause/timeout/close races, current-question restarts and future edits versus advancement.
- [ ] Test player/host disconnection and reconnect, token persistence, revoked tokens, lost replies/notices, session replacement and stale attachment cleanup.
- [ ] Test the host ownership rule under concurrent creation, lost creation replies, duplicate creation after finish, explicit closure while editing, closure versus deadline, lost closure replies and late old-room actions.
- [ ] Inject UDP loss, duplication, delay and reordering; verify sequence/revision filtering, stale-display recovery and rebinding without changing scores or phase.
- [ ] Run a 50-player load test and record the hardware/network conditions and actual supported capacity. The configured limit is not proof of capacity.
- [ ] Demonstrate at least three simultaneous rooms, each with a separate host and at least five players, with at least one client on another physical LAN device.
- [ ] Capture actual TCP and UDP traffic in Wireshark and retain screenshots and reproducible results.

The detailed scenarios are also in the protocol's later-verification section. These checkboxes are a plan, not passed results. Capacity, real LAN behavior and packet captures cannot be established by document review or arithmetic checks.

## Report and submission work still required

- [ ] Add the three team members' names and student IDs.
- [ ] Apply any page-length guidance subsequently supplied by the instructor; the provided requirements specify no page/word limit.
- [ ] Export and check the actual submission PDF. The existing rendered PDF is internal QA only.
- [ ] After implementation, write Section 3 Design and Implementation.
- [ ] Record the functional, capacity, LAN and Wireshark evidence in Section 4 Testing and Sample Runs, including the instructor's requested proof that it works.
- [ ] Write Section 5 Challenges and Limitations from actual implementation/testing experience.
- [ ] Update Section 6 References and AI assistance disclosure as the full project develops; references already accompany the sections 1 and 2 report.

The framing and message-validation layer now exists in `fatrat/protocol/`. The test server's `protocol_check/rooms.py` shows a minimal host/player room flow. The next implementation step is the real server, host and player clients built on that layer, then the remaining controls and visuals. No additional gameplay-policy choice currently blocks implementation.
