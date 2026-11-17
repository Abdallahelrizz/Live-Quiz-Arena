# How Fat-Rat works: read this first

Written 2026-11-16, updated 2026-11-17, for the team. It explains the code that exists today, in the order an examiner is likely to ask about it. Every claim names the file and function to open. Read it with the code beside you; 30 to 40 minutes.

## 1. The picture

```
 host client ──TCP──┐                      ┌── room 1 (lock, timer thread)
 player client ─TCP─┤                      ├── room 2 (lock, timer thread)
 player client ─TCP─┼──►  one server  ─────┼── room 3 (lock, timer thread)
       ...          │   (fatrat/server)    └── ...
 every client ◄─UDP─┘   countdown only
```

- One server program holds every room in memory. It is the only place that knows the correct answers, the scores and the clock. Clients only ask and display.
- The host is a client too. It can run on any machine, not only on the server machine.
- Each client has one TCP connection for everything important, and one UDP socket that only receives the countdown.

## 2. The three layers of the code

| Layer | Folder | What it does |
|---|---|---|
| Protocol | `fatrat/protocol/` | Turns bytes into messages and checks them. Used by server and clients. |
| Server | `fatrat/server/` | Sockets, threads, rooms, scoring. |
| Client | `fatrat/client/` | `core.py` does all networking. The terminal programs and bots only display or decide. |

## 3. One answer, traced from keyboard to result

A player types `2` and presses Enter.

1. `terminal_player.py` calls `session.submit_answer(choice_id)`.
2. `core.py: ClientSession.request` builds `{"v":1,"type":"SUBMIT_ANSWER","request_id":<new UUID>,"room_id":...,"data":{question_id, attempt_id, choice_id}}`. It sends no time and no name: the server knows who the connection belongs to and uses its own clock.
3. `framing.py: encode_frame` puts a 4-byte length in front of the JSON and `sendall` writes it.
4. On the server, that connection's **reader thread** (`main.py: handle_connection`) calls `recv_frame`, which reads exactly 4 bytes, then exactly that many bytes, however TCP split them.
5. `decode_json` and `validate_message` reject anything malformed.
6. `main.py: dispatch` finds the player's room and calls `room.handle`.
7. `room.py: Room.handle` takes the **room lock**, checks the message came from the player's current connection, closes the question first if its time is up (`_expire`), then runs `_submit`.
8. `_submit` checks, in order: same request seen before, a question exists, right attempt, not closed, not paused, valid choice, not already answered. It records the answer with the time left on the server's clock and queues `RESULT {accepted:true}`. No correctness is sent yet.
9. If that was the last player, `_close("ALL_ANSWERED")` scores everyone once and `broadcast` queues a separate STATE for every member.
10. The lock is released. Each connection's **writer thread** (`connection.py`) writes its queued frames.
11. The client's reader thread wakes the waiting `request()` with the RESULT, then receives STATE RESULTS and the screen prints the result.

## 4. Why TCP for this and UDP for that

| Goes over | What | Why |
|---|---|---|
| TCP | Create, join, quiz, start, answers, results, STATE, reconnect | Losing or reordering any of these would change the game. TCP delivers everything, in order. |
| UDP | `LIVE_STATUS`: time left and how many have answered, about once a second | Each snapshot replaces the previous one. If one is lost, the next one makes it irrelevant, so resending it would be pointless. |

How UDP's problems are handled (`fatrat/protocol/udp.py: LiveStatusFilter.check`, used by `client/core.py`):
- **Lost:** nothing to do. The client counts down by itself between snapshots (`countdown_ms`). After 3 s of silence it asks once over TCP (`GET_STATE`).
- **Duplicated or out of order:** every snapshot has a `seq` that only grows. A snapshot whose `seq` is not greater than the last accepted one is dropped, so the clock never jumps back.
- **From the wrong place or the wrong question:** dropped unless it comes from the server's address, for the confirmed binding, and matches the question in the last TCP STATE.
- **Nothing important depends on it:** a question closes only by TCP STATE. UDP cannot change a score.

How the server learns a client's UDP address (`room.py: bind_udp`, `confirm_udp`; `udp_service.py`): the client asks over TCP and gets a random nonce, sends it in `UDP_HELLO` over UDP, and the server confirms over TCP with `UDP_BOUND`. The datagram must come from the same IP as the TCP connection.

## 5. Message boundaries on TCP

TCP is a stream of bytes with no message edges. One `recv()` can return half a message or three at once. Fat-Rat puts the length first: 4 bytes, big-endian, then that many bytes of UTF-8 JSON (`framing.py`). `_recv_exact` keeps calling `recv` until it has exactly the bytes it needs, so partial and merged reads both work. A length of 0 or above 1 MiB, bad JSON, or a frame that stalls for 10 s closes that one connection only.

## 6. Threads

| Thread | How many | Job | Where |
|---|---|---|---|
| Main | 1 | `accept()` new TCP connections | `main.py: serve_forever` |
| Reader | 1 per client | Read a frame, check it, hand it to the room | `main.py: handle_connection` |
| Writer | 1 per client | Write queued replies to the socket | `connection.py: _write_loop` |
| Timer | 1 per room | Close the question at its deadline; send `LIVE_STATUS` | `timer.py: run`, `room.py: tick` |
| UDP | 1 | Receive `UDP_HELLO` | `udp_service.py: recv_loop` |
| Heartbeat | 1 | PING quiet connections; drop those that do not answer | `main.py: heartbeat_loop` |

A client has a TCP reader thread, a UDP receiver thread, and a reconnect thread only after a drop (`client/core.py`).

## 7. Shared data and locks

| Shared data | Lock | What could go wrong without it |
|---|---|---|
| Everything in one room: players, phase, answers, scores | That room's lock (`Room.lock`) | Two players joining at 49 both get in; an answer accepted while the timer is closing the question; a score added twice. |
| Room tables, host ownership, creation ledger | Registry lock (`Registry.lock`) | Two rooms with one PIN; one host creating two rooms at once. |
| UDP binding table | Small lock in `UdpService` | The timer reading a binding while it is being replaced. |
| A connection's outgoing frames | Its queue, written by one writer thread | Two threads writing at once would mix two frames into garbage. |

Rules that prevent deadlock and stalls:
- **Lock order:** registry lock, then room lock. A thread holding a room lock never asks for the registry lock. `NEXT_QUESTION` and `CLOSE_ROOM` can release a host's ownership, so `registry.handle` takes the registry lock first for them.
- **No socket call while a lock is held.** Replies are only put in a queue under the lock. A slow client therefore cannot freeze its room. If its queue fills (1000 frames) it is disconnected.
- **Rooms are independent.** Each has its own lock and timer, so one busy room does not slow another.

## 8. Timing and scoring

- The server measures time with a monotonic clock in milliseconds (`common.py: now_ms`).
- A correct answer scores `round(500 + 500 × time left ÷ time limit)`: 1000 for an instant answer, 500 at the last moment. Wrong or none: 0. Integer arithmetic only (`scoring.py: score`).
- The deadline is checked in two places: the timer thread, and at the start of every request (`Room._expire`). So a late answer is refused even if the timer thread is slow.
- Pause stores the time left; resume starts a new deadline from it, so a pause costs nobody points.
- Points are awarded only when the question closes (`Room._close`), once.
- Equal scores share a rank (`scoring.py: leaderboard`).

## 9. When things go wrong

| Situation | What happens | Where |
|---|---|---|
| Wrong PIN, name taken, room full | `ERROR` with a code; the connection stays open | `Room.join`, `main.py: dispatch` |
| Request not valid now (start with 4 players, answer when paused) | `ERROR`, nothing changes | handlers in `room.py` |
| Garbage bytes or bad JSON | That connection is closed; nobody else is affected | `main.py: handle_connection` |
| Player's connection drops | Player stays in the room with score and slot; can come back with its reconnect token | `Room.disconnected`, `Room.resume` |
| Host's connection drops | An open question is paused; host returns and resumes | `Room.disconnected` |
| Same request sent twice | Second one returns the first reply; nothing runs twice | `_submit`, `_host_command` |
| Host acts on an old screen | `STALE_STATE` and a fresh STATE | `_host_command` (`expected_control_rev`) |
| A bug inside a handler | Caught; only that connection closes | `handle_connection` |
| Client vanishes silently (Wi-Fi off) | No PONG within 15 s of a PING: the connection is closed and handled as a normal drop | `main.py: heartbeat_loop`, `connection.py: heartbeat` |
| Client floods the server | More than a burst of 20, then 10 per second: `RATE_LIMITED`, nothing runs | `connection.py: allow_request` |

## 10. Editing questions

Code: the `_do_edit_*`, `_do_add_question`, `_do_delete_question`, `_do_reorder_questions` methods in `room.py`.

- **Questions not played yet** can be edited, added, deleted and reordered at any time. Questions already played, and the current one, are locked (`QUESTION_LOCKED`), so results never change afterwards.
- **The current question** is edited in three steps. `EDIT_BEGIN` pauses it. `EDIT_COMMIT` replaces it and makes it a new *attempt* with a new ID and its full time; answers given to the old version are dropped and nobody is scored for them. `EDIT_CANCEL` keeps everything. Either way it stays paused until the host resumes.
- Why a new attempt ID: an answer names the attempt it was given to. An answer to the old version that arrives late names the old attempt and is refused (`STALE_ATTEMPT`).
- Every change builds a new list and validates the whole quiz before replacing the old one, so a refused change leaves nothing half-done.
- In the terminal host: `edit` changes the question on screen (EDIT_BEGIN, your answers to the prompts, then EDIT_COMMIT if you type `y`, otherwise EDIT_CANCEL), `edit <n>` a later question (EDIT_QUESTION), `move <n> <m>` reorders (REORDER_QUESTIONS), `questions` lists them. The editor never prints which choice is correct, because the host window is the shared screen. `client/terminal_host.py: edit_current`.
- If the host's program closes in the middle of an edit, the room stays in the edit. When the host comes back, `edit` continues it without a second EDIT_BEGIN.

## 11. How we know the tests test something

A test that passes even when the code is wrong proves nothing. So for the important rules, the rule was broken on purpose and the test was run again. Example (2026-11-17): eight threads create a room for the same host at the same moment. With the registry lock, exactly one room is made. With the lock removed on purpose, all eight threads made a room and the test failed. The lock was then restored. Output: `docs/test-results/2026-11-17/planted-bug.txt`. The same was done earlier for the protocol layer (11 planted bugs, all caught).

The test slows down room creation by 2 ms on purpose. Without that, the gap between "this host has no room yet" and "room saved" is so short that Python almost never switches threads inside it, and the missing lock would go unnoticed.

## 12. What is not built yet

The graphical interface. Not done: the run with a client on a second device, and the Wireshark captures. See `docs/status-and-next-steps.md`.
