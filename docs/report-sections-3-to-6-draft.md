# Fat-Rat report: draft of sections 3 to 6

Draft written 2026-11-17 by Claude Code from the code as it stands. It is not yet in the Word report. Section 3 is complete. Section 4 has the results that exist and marks, in **[TO DO]**, the evidence that only the team can produce: screenshots, the run on a second device and the Wireshark captures. Sections 5 and 6 should be read and adjusted by the team so they say what the team actually experienced.

---

## 3. Design and Implementation

### 3.1 Client-server architecture

Fat-Rat has one server program and two kinds of client, host and player, all written in Python with the standard `socket` and `threading` modules.

The server holds every room in memory and is the only authority for room membership, the question clock, which answers are accepted, and the scores. Clients never compute a result. They send requests and display the state the server sends back. The host is an ordinary client with extra rights in its own room, so it can run on any machine.

Each client keeps one TCP connection open for the whole session and one UDP socket. The code has three layers:

| Layer | Package | Responsibility |
|---|---|---|
| Protocol | `fatrat/protocol/` | TCP framing, the message catalogue, validation, the UDP snapshot filter. Shared by server and clients. |
| Server | `fatrat/server/` | Sockets, threads, the room registry, the room engine, scoring, timers. |
| Client | `fatrat/client/` | `core.py` performs all networking. The terminal host, terminal player, simulated players and the graphical interface sit on top of it and contain no socket code. |

### 3.2 Organization of threads

Server threads:

| Thread | Number | Task |
|---|---|---|
| Main | 1 | Accepts TCP connections until the server is stopped. |
| Reader | 1 per connection | Receives one frame at a time, validates it and passes it to the room. |
| Writer | 1 per connection | Writes queued outgoing frames to the socket. |
| Room timer | 1 per room | Closes the question at its deadline; sends a `LIVE_STATUS` datagram to each registered client about once per second. |
| UDP receiver | 1 | Receives `UDP_HELLO` registrations. |
| Heartbeat | 1 | Sends `PING` to quiet connections and drops those that do not answer within 15 s. |

Client threads: the caller's thread sends requests and waits for replies; a TCP reader thread receives all frames; a UDP receiver thread receives snapshots; a reconnect thread exists only after a connection loss.

One reader thread per connection was chosen because it matches the blocking socket calls directly: each thread simply waits in `recv`. With at most 50 players per room this is well within what threads handle, and it keeps each connection's code sequential and easy to reason about. A separate writer thread per connection exists so that no other thread ever blocks on a slow client.

### 3.3 Rooms and quiz state

The registry maps PINs and room IDs to rooms, records which host owns which unfinished room, and keeps a ledger of successful creation requests. A room holds its host, its players, the quiz, the phase, the current question attempt, the accepted answers and the scores.

A room is always in one phase: `LOBBY`, `OPEN`, `PAUSED`, `RESULTS` or `FINISHED`. Only the server changes the phase. After every change it sends each member a `STATE` message that is a complete snapshot filtered for that recipient: a player receives choice identifiers and symbol positions but never question text, answer text or the correct answer while a question is open; the host also receives the texts, the quiz and the participant list. Because every `STATE` is complete, a client that missed anything simply replaces what it shows.

Each time a question is opened it becomes a new *attempt* with its own identifier. Answers name the attempt, so an answer to an earlier version of a question can never be counted for a later one.

Rooms live until the server stops. A host is recognised by a random token saved on the host's device, not by an account; the registry allows one unfinished room per token.

### 3.4 Shared resources and synchronization

| Shared resource | Accessed by | Protection |
|---|---|---|
| All state of one room | Reader threads of its members, its timer thread, the UDP thread | One lock per room |
| Room tables, host ownership, creation ledger | Reader threads creating, joining or finishing rooms | One registry lock |
| UDP binding table | Reader threads, the UDP thread | A small dedicated lock |
| One connection's outgoing bytes | Any thread that produces a message for that client | A queue drained by a single writer thread |
| A connection's heartbeat and rate counters | Its reader thread, the heartbeat thread | A small lock per connection |

Race conditions this prevents, each covered by a test:

- **Capacity.** The player count is checked and the player added while holding the room lock. When three clients race for one free slot, exactly one is accepted.
- **Deadline against answer.** The timer thread and an answering player's reader thread both need the room lock. In addition, every request first closes the question if its deadline has passed, so a late answer is refused even when the timer thread has not yet run.
- **Single scoring.** Points are awarded in one place, when the question closes, under the lock. No answer can be scored twice and no result can be seen before the close.
- **One room per host.** Creation checks and inserts under the registry lock, so two simultaneous creation requests from one host cannot both succeed.
- **Replaced connections.** A request is processed only if it came from the member's current connection. Requests still queued from a connection that was replaced, removed or closed are discarded.

Deadlock is avoided by a fixed order: the registry lock is always taken before a room lock, never after, and no thread holds two room locks. The two requests that can release a host's ownership (`NEXT_QUESTION` on the last question and `CLOSE_ROOM`) take the registry lock first for that reason.

No socket operation is performed while a lock is held. Handlers place replies in the connection's queue while holding the room lock, which fixes their order, and the writer thread sends them afterwards. A slow client therefore cannot stall its room; if its queue reaches 1000 frames it is disconnected.

### 3.5 Timing policy

The server measures time with a monotonic millisecond clock, which is unaffected by changes to the wall clock. When a question opens the server records its deadline. The time of an answer is the server's clock at the moment the answer is accepted under the room lock; clients send no timestamps. This is simple and cannot be falsified, at the cost that network delay counts against the player.

Players learn the remaining time from `LIVE_STATUS` datagrams over UDP, about one per second, and count down locally between them. The display is an estimate; only the server's deadline decides.

Pausing stores the remaining time and resuming sets a new deadline from it, so a pause never costs points. If the host's connection is lost while a question is open, the question is paused until the host returns and resumes it. A player's disconnection does not shorten the question: the player keeps the right to return and answer.

A question closes when every eligible player has answered, when the deadline passes, or when the host closes it.

### 3.6 Scoring policy

A correct answer earns between 500 and 1000 points depending on speed:

    score = round(500 + 500 × remaining time ÷ time limit)

A wrong or missing answer earns 0. The computation uses integers only, `500 + (1000·r + L) // (2·L)`, so every run gives the same result. Scores accumulate over questions. The leaderboard uses dense ranks: players with equal totals share a rank and the next total takes the next rank. Players who left or were removed keep their row and are labelled.

### 3.7 Other design decisions

- **Reconnection.** Each player receives a random reconnect token on joining. After a connection loss the client reconnects and resumes the same identity, score and submitted answer. A second connection with the same token replaces the first.
- **Repeated requests.** Every request carries a unique identifier. A repeated answer or host command returns the original reply without running again.
- **Stale host commands.** Host commands carry the revision of the state the host last saw. If the room has changed, the server refuses with `STALE_STATE` and sends the current state.
- **Editing.** The host may add, delete, reorder and edit questions not yet played. Editing the current question pauses it and, on commit, restarts it as a new attempt with its full time; earlier answers to it are discarded unscored. Played questions are locked.
- **Limits.** 5 connected players to start, at most 50 active players per room, 2 to 8 choices, 1 to 30 questions, 1 to 300 seconds per question, frames up to 1 MiB, datagrams up to 512 bytes, 10 requests per second per connection with a burst of 20.
- **Heartbeat.** A client that disappears without closing its connection is detected by `PING`/`PONG` and then treated like any other disconnection.

---

## 4. Testing and Sample Runs

### 4.1 What was tested and how

| Level | What | Count | Result |
|---|---|---|---|
| Protocol unit tests | Framing with split and merged reads, every message type, quiz limits, the UDP filter | 57 | all pass |
| Room engine unit tests | Rules of the room with a controllable clock and no sockets | 39 | all pass |
| End-to-end tests | Real server and clients over TCP and UDP sockets on one machine | 14 | all pass |
| Client file tests | The saved host identity and reconnect tokens; quiz files | 8 | all pass |
| Rooms check | Scripted clients: largest quiz, 50 players, start gate, race for the last slot, three rooms | 40 checks | 40/40 |

Raw outputs are in `docs/test-results/2026-11-16/` and `docs/test-results/2026-11-17/`. All of the above ran on one computer over the loopback interface on 16–17 November 2026.

### 4.2 Concurrency results

- 46 players joining the same room at the same moment were all accepted; the 51st player was refused with `ROOM_FULL`.
- Three clients racing for one free slot: exactly one accepted.
- 49 simultaneous answers in a full room: each accepted exactly once.
- Three rooms of five players ran at the same time with separate PINs, questions, timers and scores; no client received a message from another room. One room waited for its own timeout while the other two closed as soon as everyone had answered.
- One room of 50 simulated players completed a three-question quiz: 150 answers accepted.
- Eight threads sent `CREATE_ROOM` for the same host at the same moment, 20 times over: each time exactly one room was created and the other seven were told which room it was. To check that this test really depends on the lock, the registry lock was removed on purpose: all eight threads then created a room, and the test failed. The lock was restored afterwards.

### 4.3 Error-handling results

- Invalid PIN, duplicate name, empty name, full room, finished room: refused with the specific error code; the connection stays usable.
- Duplicate, late, paused and invalid-choice answers: refused; the first accepted answer stands.
- A frame with an impossible length and a frame containing invalid JSON each closed only the offending connection; the room and its other clients continued.
- A player whose connection was cut reconnected automatically and found its answer and score intact.
- A host whose connection was cut: the question paused; the host returned to the same room and resumed.
- A client that stopped answering `PING` was dropped while the others stayed connected.
- A client sending 40 requests at once was answered `RATE_LIMITED` after its burst; another client was unaffected.
- The reply to `CREATE_ROOM` was lost on purpose (the connection was cut as it arrived). When the host program was started again it sent the same saved request, the server recognised it, and the host returned to the room it had created; no second room appeared.
- The host ended the room at the moment the question's time ran out: the question was closed and scored first, and the host's command, decided on the old state, was refused with `STALE_STATE`. Ending the room while editing discarded the edit and the unscored question.

### 4.4 UDP loss, duplication and reordering

With the server's test option `--impair-udp`, every 4th datagram to each client is dropped, every 5th is sent twice and every 7th is delivered after the next. In the automated test the client rejected the duplicates and the out-of-order datagrams, the displayed countdown never increased, and the question still closed and showed results, because the result travels over TCP. When UDP was blocked completely, the client asked once for the state over TCP after the quiet period, marked its countdown as approximate, and the question still closed and showed results.

### 4.5 Still to be produced by the team

- **[TO DO] Screenshots** of the main operations: server start, room creation with PIN, players joining, a question on host and player, results, final ranking, an error case.
- **[TO DO] Second device.** Show how the server's IP address was obtained (`ipconfig getifaddr en0` on macOS, `ipconfig` on Windows) and a client on another device using it. Record device types and the network used.
- **[TO DO] Three concurrent rooms on the network**, at least one real client on another device; the rest may be simulated players.
- **[TO DO] Wireshark captures** with the filter `tcp.port == 43000 || udp.port == 43001`: the TCP three-way handshake; one request frame showing the 4-byte length followed by JSON; a `STATE` message; the `UDP_HELLO` datagram and the `UDP_BOUND` confirmation over TCP; a sequence of `LIVE_STATUS` datagrams with increasing `seq`; a connection close.
- **[TO DO] 50 players across real devices**, if devices are available; otherwise state that the 50-player result is from simulated clients on one machine.

---

## 5. Challenges and Limitations

*Draft. Keep what matches the team's experience and add your own.*

**Challenges met during development**

- **A command sent before the previous result was visible.** The server replies `RESULT` and then sends the new `STATE`. A host program that issued a second command immediately after the first reply still held the old revision and was refused with `STALE_STATE`. An end-to-end test exposed it. The client now waits briefly for the `STATE` that follows a successful host command.
- **Order of messages across threads.** Several threads produce messages for the same client. Sending directly from each would let frames interleave or arrive in a different order from the one in which the room changed. Queuing every message while the room lock is held, with one writer thread per connection, solved both.
- **A deadline that depends on a thread waking up.** Relying only on a timer thread would let an answer slip in after the deadline if that thread were delayed. Each request therefore checks the deadline itself before doing anything else.
- **Telling the old connection from the new one.** After a reconnect, messages from the old connection may still be waiting. Each request is checked against the member's current connection and discarded otherwise.
- **Testing time-dependent rules.** Waiting for real timers made tests slow and imprecise. The room engine is tested with a replaceable clock, so a deadline can be reached to the millisecond.

**Limitations**

- All state is in memory. Stopping the server ends every room; there is no recovery after a server crash.
- Nothing is encrypted or authenticated beyond random tokens. Anyone on the same network who captures the traffic can read it. This is outside the scope of the project.
- A player who never receives the reply to its first join cannot recover that identity and must join under another name.
- Network delay counts against the player, because the server uses its own receive time.
- Tested capacity is 50 simulated players on one machine. **[TO DO: update after the multi-device run.]**
- **[TO DO: state of the graphical interface at submission.]**

---

## 6. References

*Keep the references already listed with sections 1 and 2 and add:*

- Python Software Foundation. *socket — Low-level networking interface*; *threading — Thread-based parallelism*; *queue — A synchronized queue class*; *json*; *struct*; *secrets*; *unittest*. Python 3 documentation, https://docs.python.org/3/library/
- J. F. Kurose and K. W. Ross, *Computer Networking: A Top-Down Approach*: the chapters on the application layer, socket programming and the transport layer.
- Course material: *Project Requirements*, *Wireshark: Getting Started*, and the HTTP Wireshark sample lab.
- Wireshark Foundation, *Wireshark User's Guide*, https://www.wireshark.org/docs/

**Use of AI tools.** OpenAI Codex and Anthropic Claude (Claude Code) were used during this project: to review the protocol design, to draft parts of the report, and to write much of the implementation and its tests under the team's direction. The team reviewed the design decisions, ran the tests, and studied the code in order to explain it. **[TO DO: the team should word this honestly in its own terms.]**
