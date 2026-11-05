# Fat-Rat

CSC430 Computer Networks course project at Lebanese American University.

**Status as of 5 November 2026:**
- Sections 1 and 2 describe the reviewed design.
- The section 2 protocol has been built and tested. Server and client ran on one Mac: 57 automated tests plus 227 network checks, all passing. The run with a client on a second device and the Wireshark captures are still to do.
- The quiz game itself (real server, host and player clients, interface) has not been built.

Fat-Rat is a planned multiplayer quiz using Python raw TCP/UDP sockets and threading. Each room has one noncompeting host, starts with at least five connected players, and supports a design limit of 50 ACTIVE players excluding the host. Quizzes contain 1–30 questions with 2–8 choices. Players select cheese symbols; Mr Fat Rat's feeding progress represents confirmed cumulative points. A saved host identity owns one unfinished room at a time and can create another after finishing or closing it.

- [Current status and remaining work](docs/status-and-next-steps.md)
- [Protocol test: how to run it, what it checks, results](docs/protocol-test.md)
- [Agreed gameplay](docs/gameplay-decisions.md)
- [Application-layer protocol](docs/section-2-protocol.md)
- [Review findings and dispositions](docs/protocol-review.md)
- [Sections 1 and 2 Word report](output/docx/CSC430_Fat_Rat_Sections_1_and_2.docx)

The report sections are due **6 November 2026**, as confirmed by Abdallah. The supplied course requirements are in [source_documents](source_documents/).

## Running the protocol test

Python 3.8 or newer, standard library only. Run from this folder (on Windows use `py` instead of `python3`):

```bash
python3 -m unittest -v                                          # automated tests, about 2 s
python3 -m protocol_check.server                                # test server: prints its IP and a PIN
python3 -m protocol_check.client --server <IP> --pin <PIN>      # on another device: protocol check
python3 -m protocol_check.rooms_client --server <IP>            # on another device: 50-player maximum, 3 rooms
```

`protocol_check/server.py` is a test server for the protocol, not the game server. See [docs/protocol-test.md](docs/protocol-test.md) for Wireshark steps and what each check means.
