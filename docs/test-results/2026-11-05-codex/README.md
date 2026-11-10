# Independent protocol rerun 5 November 2026

Codex reran Claude Code's existing protocol suite. All four commands exited successfully. Raw outputs and summary.json record the results. Server and clients ran on one Mac using loopback, not two physical devices. These are selected protocol/test-server checks, not full-game validation. The sandbox initially prevented local socket binds; the recorded successful runs were performed with local socket access permitted.

Commands from the repository root:

```bash
python3 -m unittest -v
python3 -m protocol_check.server --ip 127.0.0.1 --tcp-port 43100 --udp-port 43101
python3 -m protocol_check.client --server 127.0.0.1 --pin <printed-pin> --tcp-port 43100 --udp-port 43101 --name codex-one
python3 -m protocol_check.client --server 127.0.0.1 --pin <printed-pin> --tcp-port 43100 --udp-port 43101 --name codex-five --players 5 --skip-slow
python3 -m protocol_check.rooms_client --server 127.0.0.1 --tcp-port 43100
```

57/57 unit tests and 227/227 network assertions passed (36 + 151 + 40). Repeated checks are included in those totals. The 10-second stalled-frame check ran in the one-client suite and was skipped in the five-client suite. The room suite checks the 50-player admission cap and 51st rejection; it does not establish throughput/capacity on multiple devices. The test server was stopped after these runs.

After the Word report is regenerated, the report-consistency tests are rerun separately so message/error-table agreement refers to the delivered document.
