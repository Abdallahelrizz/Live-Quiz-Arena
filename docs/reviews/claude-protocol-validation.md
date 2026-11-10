# Claude review of protocol-validation report text

2026-11-05. Focused wording review based on stated rerun evidence; not a fresh execution or full protocol review.

**Verdict:** The numbers are accurate (36 + 151 + 40 = 227, plus 57 unit tests). Python version, address, ports, the 0.5 s note, the scope limits and the Section 4 deferral all match the facts you gave. I found no material overclaim about game functionality. A few items still need fixing.

**Fix these:**

1. **"independently rerun"** overstates. The rerun was done by the team's own agent on the same Mac, not by a third party. Use "rerun".

2. **Ports 43100/43101 against the defaults.** §2, the README and CLAUDE.md give TCP 43000 and UDP 43001. Without explanation, the report looks inconsistent with its own §2. Add something like "(non-default ports chosen for this rerun; the protocol default is 43000/43001)". Also check that `docs/protocol-test.md`, which the report cites, describes this loopback rerun on 43100/43101. The project notes describe the default ports and an earlier run over the Mac's LAN address.

3. **"retained in the repository … docs/test-results/2026-11-05-codex/"** is not yet true. Git status shows `docs/test-results/` as untracked. It must be committed or included in the submission before the report says this.

4. **"fatal-input recovery"** (single-client row) is ambiguous. It can read as reconnect or recovery, which the last paragraph says was not tested. Say what was checked, for example "server closes the offending connection with an error and keeps serving other clients", if that is what the check does.

**Smaller wording fixes:**

5. **"not exercised end to end"** suggests these features were partly exercised. More precise: their message formats are covered by the unit validation tests, but the behaviour is not implemented in the test server.

6. The five-client row mentions "the slow timeout case", but the single-client row never introduces it. Add "10 s stalled-frame timeout" to the single-client coverage.

7. **"Largest configured quiz"** should read "largest quiz allowed by the protocol limits". You could add the 428,179-byte PUT_QUIZ figure.

The "50-player admission limit, 51st rejection" wording is fine because it does not claim 50 connected clients. Keep it that way if you add the "49 connected players received STATE" figure.

As you asked, I used no tools. I did not open the vault rules, `docs/protocol-test.md` or the test outputs. This review relies only on your stated facts, the git status snapshot and CLAUDE.md.

## Codex disposition

Accepted: use rerun rather than implying third-party independence; explain non-default test ports; describe bad-client isolation and the single-client 10-second timeout; distinguish schema checks from unsupported behavioral scenarios. Describe the quiz by concrete tested bounds rather than asserting an absolute largest wire message. Raw outputs exist locally under docs/test-results/2026-11-05-codex/ and accompany the project; no commit/push is claimed or performed, because Abdallah handles that.
