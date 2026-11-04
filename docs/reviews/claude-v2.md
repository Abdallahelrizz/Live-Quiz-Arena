# Second review: Section 2 protocol v2

**Verdict:** v2 has no remaining correctness blockers that require redesign. Finding 1 is a material ambiguity and should be fixed before section 2 is frozen. The other findings are low-severity clarifications. This is a design review only; it says nothing about implemented or tested behavior.

## Codex rejections: I accept all three

- **`control_rev` in UDP instead of my attempt + phase + seq matching.** My proposal fails in one case. Suppose the client receives no PAUSED snapshot, and a pre-pause OPEN packet is delayed past the RESUME STATE. That packet's `seq` is higher than the last one accepted, and its attempt and phase both match, so the client applies it. A monotonic `control_rev` rejects it. I withdraw the proposal.
- **`expected_control_rev` on host commands.** Phase-only checks fail with fast bots. Take a duplicate NEXT_QUESTION: the first advances to Q2, and Q2 closes by ALL_ANSWERED before the second arrives. The second NEXT then lands in RESULTS and skips Q2's results. Replays across connections are already blocked by the generation check, but this replay happens within one connection. Accepted.
- **Retained LIVE_STATUS identifiers and the bounded host cache.** The eviction argument holds, because every successful host mutation increments `control_rev`. Accepted.

## Verified as fixed

- Late answers now reach ANSWER_LATE.
- The pause, editing and `host_connected` invariants hold.
- The score formula is exact half-up: r=1, L=1000 gives 501, and r=L gives 1000.
- The split between fatal and nonfatal errors is defined.
- The PING/PONG envelope is defined.
- The UDP rebind after resume appears in the diagram.
- STATE is per-recipient, and `live` is always present.
- All 27 catalogue types are covered by the envelope table, and every error code used in the text appears in the error table.

## Findings

**1. MEDIUM: STATE recipients are undefined, so a `control_rev` change can black out UDP for players**

Players accept LIVE_STATUS only on an exact `control_rev` match. The spec never says who receives STATE for each trigger.

- EDIT_QUESTION on a future question and REMOVE_PLAYER both increment `control_rev` without changing phase.
- If only the host receives that STATE, every player discards every snapshot. This lasts until the 3-second stale rule's GET_STATE repairs it.
- The roster has the same ambiguity. The player leaderboard contains zero-score rows for every participant, yet the diagram sends the join STATE to the host only.

**Action:** add a recipient rule:
- Any change to `control_rev` or `host_connected`: send STATE to every attached session.
- Join or removal: send STATE to everyone, because the roster and leaderboard change.
- Player connect or disconnect: send STATE to the host only.

Then make the typical-session diagram match.

**2. LOW-MEDIUM: does a host session replacement pause the quiz?**

A host client usually reacts first, either to its own EOF/reset or to its 20-second inactivity timer. It can therefore RESUME_SESSION before the server's 15-second PONG deadline fires. In that case no host-disconnect event ever happens, and OPEN keeps running while the host was blind.

**Action:** define what replacement does. Suggested default: replacing a host attachment while OPEN applies the host-disconnect transition first, then attaches.
- The phase becomes PAUSED with reason HOST_DISCONNECTED.
- `control_rev` increments.

This matches the agreed rule "host reconnects and explicitly resumes." Label it as a default.

**3. LOW: LIVE_STATUS consistency and source address**

- **(a) Read the snapshot fields together.** The ticker must take attempt, `control_rev`, `remaining_ms` and both counts from one committed state, either under the room lock or from an immutable snapshot.
  - Otherwise a packet can pair the post-EDIT_COMMIT `control_rev` with the pre-commit `answered_count`, and it will pass the client filter.
  - Derive `remaining_ms` with the same `max(0, deadline − now)` rule.
- **(b) Fix the UDP source address.** Clients check the server's source IP. A server UDP socket bound to `0.0.0.0` on a multi-homed laptop (VPN or a second interface) may reply from a different address than the one the client dialed.
  - Bind the UDP socket to the advertised IPv4 address, or document the limitation.

**4. LOW: the size budget skips edits**

The 512 KiB quiz cap and the host-STATE budget are checked only "before accepting a quiz or join."
- EDIT_QUESTION and EDIT_COMMIT can enlarge a question within the per-field limits and exceed both caps.
- While an edit is active, the host STATE also carries the frozen attempt snapshot alongside the reusable quiz.

**Action:** run both checks on every command that changes the quiz. A failure returns INVALID_QUIZ with no mutation.

**5. LOW: error mappings are missing**

Every case below is already rejected, so correctness is safe. Tests still need a single expected code for each. Suggestions:

| Case | Suggested code |
|---|---|
| EDIT_QUESTION targeting the current OPEN/PAUSED question | INVALID_STATE ("use EDIT_BEGIN") |
| EDIT_BEGIN or EDIT_COMMIT naming a question other than the current one | STALE_ATTEMPT |
| SUBMIT for the previous attempt while the next question is OPEN (2.3 only covers RESULTS/FINISHED) | ANSWER_LATE |
| EDIT_QUESTION before any quiz is loaded | INVALID_STATE |
| Join refused by the byte budget | SERVER_BUSY with `retryable:false` (2.3 currently says "temporary SERVER_BUSY") |

Also unify the terms "ordinary PAUSED" (2.3) and "PAUSED not editing" (2.5). As written, a HOST_DISCONNECTED pause is closable.

**6. LOW: policies that are proposed but read as settled**

These appear in the spec body without a "proposed default" tag, although the agreements do not cover them:
- no early close when the eligible set is empty (2.4)
- name reservation surviving removal (2.2)
- FINISHED rooms rejecting joins, with no replay in the same room

The header disclaimer covers them in principle, but it will likely be lost when section 2 is condensed into the report. Tag each one inline.

## Bottom line

After finding 1 is resolved, I see no remaining design blockers. Findings 2–6 are clarifications that can go into the same revision. Nothing in this review is implementation, test or packet-capture evidence.

*Vault:* I used no tools, as instructed. The protocol-review note and `log.md` were not updated, so Codex should record this second review.
