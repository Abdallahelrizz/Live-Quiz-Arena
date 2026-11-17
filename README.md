# Fat-Rat

CSC430 Computer Networks course project, Lebanese American University, Fall 2026.
Team: Abdallah Elrizz (202301345), Rayan Madi (202401943), Tala Daouk (202304053).

Fat-Rat is a multiplayer, Kahoot-style quiz written in Python with raw TCP and UDP sockets and threads. A host creates a room and gets a PIN, players join with the PIN and a name, and the server runs timed multiple-choice questions, scores the answers and shows a leaderboard after each question.

**Status on 17 November 2026**

- Report sections 1 and 2 are submitted (`submitted_documents/`).
- The game server, the terminal host and player clients and the simulated players are built and pass their tests **on one Mac**.
- The whole section 2 protocol is implemented, including heartbeat, rate limiting and question editing during play.
- Not done yet: the run with a client on a second device, Wireshark captures, the graphical interface, and the final report (sections 3 to 6 exist as a draft in `docs/report-sections-3-to-6-draft.md`).

Details: [status and remaining work](docs/status-and-next-steps.md), [the full plan](docs/project-plan.md), [how the code works](docs/study/01-how-fat-rat-works.md), [GUI design brief](docs/gui-design-brief.md).

## Requirements

Python 3.8 or newer. No packages to install: the server, the clients and the tests use only the standard library. Run every command from this folder. On Windows use `py` instead of `python3`.

## Playing

1. **Start the server** on one computer. It prints the IPv4 address the others must use.

   ```bash
   python3 -m fatrat.server
   ```

   To check the address yourself: `ipconfig getifaddr en0` on macOS, `ipconfig` on Windows. All devices must be on the same network. If a firewall asks, allow Python to accept incoming connections (TCP 43000, UDP 43001).

2. **Start the host** on any computer. It creates the room and shows the PIN and the questions.

   ```bash
   python3 -m fatrat.client.terminal_host --server <server IP> --quiz quizzes/networks.json
   ```

3. **Each player joins** from their own computer with the PIN.

   ```bash
   python3 -m fatrat.client.terminal_player --server <server IP>
   ```

4. The host types `start` once at least five players are connected. Not enough people? Add simulated players:

   ```bash
   python3 -m fatrat.client.bot --server <server IP> --pin <PIN> --players 5
   ```

Host commands: `load <file>`, `questions`, `append <file>`, `edit` (the question on screen), `edit <n>`, `move <n> <m>`, `delete <n>`, `start`, `pause`, `resume`, `close`, `next`, `players`, `kick <name>`, `end`, `quit`. Editing the question on screen pauses it; once saved it restarts with its full time.
Player: type the number of your answer; `leave` to leave for good; `quit` to close and come back later.

Three rooms running at once, hosts and players all simulated:

```bash
python3 -m fatrat.client.bot --server <server IP> --rooms 3 --players 5 --quiz quizzes/quick-demo.json
```

## Quiz files

A quiz is a JSON file you can write by hand; see `quizzes/networks.json`. Each question has `text`, 2 to 8 `choices`, the position of the `correct` choice (starting at 0) and `time_seconds` (1 to 300). A quiz has 1 to 30 questions.

## Tests

```bash
python3 -m unittest -v                                      # 118 tests, about 15 s
python3 -m protocol_check.rooms_client --server <server IP>  # 40 checks against a running server: 50 players, 3 rooms
```

To show how the client copes with UDP loss, duplication and reordering, start the server with `python3 -m fatrat.server --impair-udp` (test mode: it drops, repeats and delays countdown datagrams on purpose).

Results and raw outputs are in `docs/test-results/`. The earlier protocol-only test and its own test server are described in [docs/protocol-test.md](docs/protocol-test.md).

## Layout

| Path | Contents |
|---|---|
| `fatrat/protocol/` | The application-layer protocol: TCP framing, message catalogue, validation, UDP filter. |
| `fatrat/server/` | The game server: sockets and threads, rooms, scoring, timers, UDP. |
| `fatrat/client/` | `core.py` (all client networking), terminal host and player, bots, quiz files, saved identity. |
| `quizzes/` | Sample quiz files. |
| `tests/` | Automated tests: protocol, room engine, and full games over real sockets. |
| `protocol_check/` | The earlier protocol test server and its check clients. |
| `docs/` | Protocol specification, gameplay decisions, plan, status, study notes, test results. |
| `source_documents/`, `submitted_documents/` | Course PDFs; the submitted sections 1 and 2. |

Ports: TCP 43000, UDP 43001. Wireshark filter: `tcp.port == 43000 || udp.port == 43001`.
