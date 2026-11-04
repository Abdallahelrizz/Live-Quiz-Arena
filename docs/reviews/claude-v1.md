# Adversarial review: Section 2 protocol draft v1

**Verdict: not clean.** The core design is sound, and I would keep it as is:

- length-prefixed framing
- the property-based TCP/UDP split
- server-only timing
- scoring only at close
- role filtering of answer text
- a single room lock with writes outside the lock
- a disconnect never shrinks eligibility
- edit-restart semantics

However, one structural flaw (#1) will break live countdowns and host commands during normal play. The idempotency layer (#3) contradicts itself and isn't needed.

**Mandated vs. ours.** The course mandates these: meaningful TCP and UDP use, TCP framing, handling of UDP loss, duplication and reordering, a full message specification, rejection of invalid, duplicate and late answers, independent rooms, and a final leaderboard. These are our choices and are not mandated: mid-question editing, pause, reconnect tokens, removal, late join, symbol-only player screens, the UDP bind handshake, the replay cache and strict JSON. Our additions roughly double the state machine, so they must not destabilize the mandated core.

---

### 1. HIGH: `state_rev` is overloaded, so membership churn breaks UDP and host commands

`state_rev` increases on every join, disconnect and reconnect. Two things break:

- **UDP.** LIVE_STATUS must match the last STATE's `state_rev`, and any snapshot with a newer revision is discarded. Players get no membership data, and the diagram sends the membership STATE only to the host. One late join or Wi-Fi blip therefore makes every player discard every snapshot. Each player goes stale and polls GET_STATE. If you instead broadcast STATE on every connection change, you get O(n) TCP snapshots per blip, which contradicts "TCP sync is exceptional."
- **Host commands.** Every `expected_rev` command races with joins and disconnects. If the host presses Next while a player reconnects, the host gets a spurious STALE_STATE.

`expected_rev` is also unnecessary. Phase checks already make duplicates harmless: a second NEXT arrives in OPEN, a second CLOSE arrives in RESULTS, and a second COMMIT arrives when no edit is active, so each gets INVALID_STATE.

**Fix:**
- Keep `state_rev` only for ordering STATE snapshots.
- Remove `state_rev` from LIVE_STATUS. Add `phase` (OPEN/PAUSED) to its data, and match UDP on `attempt_id` + `phase` + strictly increasing `seq`. Seq handles reordering. The phase/attempt mismatch rejects packets sent before or after a transition.
- Replace `expected_rev` with targeted IDs:
  - `attempt_id` on PAUSE, RESUME, CLOSE and EDIT_BEGIN/COMMIT/CANCEL. A stale attempt gets STALE_ATTEMPT.
  - `question_id` on NEXT_QUESTION.
  - Nothing on PUT_QUIZ, EDIT_QUESTION or REMOVE_PLAYER, which are idempotent replacements or removals.

### 2. HIGH: late answers can never receive ANSWER_LATE

Section 2.4 makes every handler first close an expired OPEN attempt. Section 2.3 then checks attempt, phase and deadline, in that order. After the auto-close the phase is RESULTS, so a late answer gets INVALID_STATE and the deadline check can never run. Host-closed questions have the same problem. Requirement 4 explicitly names late submissions.

**Fix:** after the auto-close step, apply these rules:
- If `attempt_id` equals the current or most recently closed attempt and the phase is RESULTS or FINISHED, return ANSWER_LATE.
- If the attempt is older, return STALE_ATTEMPT.
- Define the OPEN-phase lateness rule in integer ms (see #6).

### 3. MEDIUM-HIGH: the replay cache contradicts itself and isn't needed

- A replayed GET_STATE returns the cached `RESULT {}` without the STATE, so the retry loses its whole purpose. Replayed JOIN and RESUME requests lose their follow-up STATE the same way.
- Retryable errors (RATE_LIMITED, SERVER_BUSY) are cached. A retry with the same ID gets the same error forever.
- The pre-authentication cache keyed on connection + request_id never fires. TCP either delivers in order or the connection dies, so real retries only happen on a new connection.
- The cache caps at 10,000 outcomes and rooms live until shutdown. RATE_LIMITED "until session cleanup" is therefore a permanent lockout.
- Duplicates are already harmless. A replayed SUBMIT gets ALREADY_ANSWERED, and a replayed host command gets INVALID_STATE or STALE_ATTEMPT (after #1).

**Fix:**
- Delete the persistent cache and REQUEST_ID_REUSED. Keep `request_id` only to correlate replies.
- Reconciliation rule: after RESUME_SESSION, the client drops all in-flight requests, reads the STATE (`phase`, `attempt_id`, `submitted_choice_id`) and re-issues an action only if the STATE doesn't reflect it.
- Add `choice_id` to ALREADY_ANSWERED.
- Rewrite the last note in the edit diagram to match.
- If you keep any cache, exempt GET_STATE, never cache retryable errors, bound it to the last 32 outcomes per actor, and re-send a fresh STATE after replaying an identity RESULT.

### 4. MEDIUM: the pause/edit status model is inconsistent

- `pause_reason: EDITING` duplicates the `editing` boolean.
- `pause_reason` after EDIT_CANCEL and after a host reconnect is undefined.
- If the host drops during an edit, one `pause_reason` can't represent both causes.
- EDIT_BEGIN's "remember pre-edit pause status" is never used, because EDIT_CANCEL always stays PAUSED.
- In LOBBY or RESULTS, players can't tell that the host is gone.

**Fix:**
- Define `pause_reason ∈ {MANUAL, EDITING, HOST_DISCONNECTED}`, null unless PAUSED.
- Delete `editing` and EDIT_COMMITTED. A change of `attempt_id` already signals a restart.
- Transitions:

  | Event | New `pause_reason` |
  |---|---|
  | EDIT_BEGIN | EDITING |
  | EDIT_COMMIT or EDIT_CANCEL | MANUAL |
  | Host drops while OPEN | HOST_DISCONNECTED |
  | Host reconnects | MANUAL |
  | Host drops while EDITING | stays EDITING |

- Add `host_connected: bool` to every STATE.
- Delete the "remember pre-edit pause status" clause.
- On RESUME_QUIZ, re-check the all-answered condition. A removal during PAUSED can make every remaining player answered.

### 5. MEDIUM: STATE and identity schema holes

- STATE varies per **recipient**, not just per role (`self`, `own_result`, `submitted_choice_id`). Say so explicitly, because one serialized player STATE cannot be broadcast to everyone.
- `live` appears only in GET_STATE replies but is missing from the required fields, while unknown fields are rejected. Make `live: {answered_count, eligible_count} | null` always present.
- "Leaderboard initially empty *or* zero-score rows" leaves the choice open. Pick zero-score rows for all non-removed players, which doubles as the lobby roster.
- RESULT and `self` use `actor_id`, but participants, RankRow and REMOVE_PLAYER use `player_id`. State that `player_id == actor_id`.
- The lifetime of `session_id` is undefined. Recommend a new value per attachment (create, join, resume), invalidated on replacement.
- `question_id` and `choice_id` are called "opaque UUIDs," yet the host client authors them in PUT_QUIZ. State that the host client generates UUIDv4s and the server validates their format and uniqueness (choice IDs within a question, question IDs within the quiz).
- Apply strict validation only to client→server input. Clients should ignore unknown fields.
- Does the host's CREATE_ROOM `username` reserve that name against players? Decide, or drop the field.
- The envelope table has no rows for PING/PONG. Add them: no `request_id`, no `room_id`.

### 6. MEDIUM: deadline and score arithmetic are underspecified

The draft never says how `remaining_ms` is derived from the monotonic clock, or whether `remaining_ms == 0` counts as late.

**Fix:**
- `now_ms = time.monotonic_ns() // 1_000_000`
- `remaining_ms = deadline_ms − now_ms`
- Late if `remaining_ms ≤ 0`.
- Pause stores `remaining_ms`. Resume sets `deadline_ms = now_ms + remaining_ms`.
- Score a correct answer as `500 + (1000*r + L) // (2*L)`. This is exact half-up rounding with no dependence on float evaluation order. Check: r=L gives 1000, and r=1, L=1000 gives 501.
- `answered_count` counts accepted answers from eligible (non-removed) players only.

### 7. MEDIUM: the frame limit can block the server's own host STATE

The field limits allow about 400,000 characters of quiz text. PUT_QUIZ can fit under 1 MiB while the host STATE (quiz + participants + leaderboard + last_result) exceeds it. That gets worse if `json.dumps` escapes non-ASCII as 6–12 bytes per character.

**Fix:**
- Use `ensure_ascii=False, separators=(",",":")`.
- Cap the serialized quiz at 768 KiB, or lower the text limits, so the host STATE always fits.

### 8. MEDIUM: gaps in error handling

- "Fatal behavior follows framing rules" is ambiguous. Specify:
  - Fatal (send ERROR, then close): framing errors, invalid UTF-8 or JSON, a non-object payload, an unsupported version.
  - Non-fatal (connection stays open): schema errors in well-formed JSON get a correlated INVALID_MESSAGE.
- Missing error codes:
  - NOT_PLAYER: the host sends SUBMIT_ANSWER.
  - UNKNOWN_PLAYER: REMOVE_PLAYER targets an unknown, host or already-removed player.
  - ALREADY_ATTACHED: a second CREATE or JOIN arrives on an attached connection.
  - START_QUIZ with no quiz loaded: state that it returns INVALID_STATE.
- Add a `retryable` column to the error table.
- Client rule: ignore and log unknown server types and RESULTs whose `reply_to` is unknown.

### 9. MEDIUM: UDP registration hazards for the demo

- The IP equality check fails in two ways:
  - A client connects TCP to `127.0.0.1` but sends HELLO to the LAN IP. This is likely for a host or test bots running on the server machine.
  - A dual-stack IPv6 TCP socket reports `::ffff:a.b.c.d` while UDP reports `a.b.c.d`.

  **Fix:** send UDP_HELLO to the exact IP literal used for TCP, and use AF_INET for both sockets.
- Resume disables the old binding, but neither section 2.7 nor the edit diagram shows REQUEST_UDP_BIND after RESUME_SESSION. Add it.
- Limit the 3-second stale rule to OPEN and PAUSED. Otherwise clients in LOBBY or RESULTS poll GET_STATE every 5 seconds forever.
- State that the client discards LIVE_STATUS until UDP_BOUND arrives.
- Drop `room_id`, `session_id`, `question_id` and `state_rev` from the LIVE_STATUS envelope, because `binding_id` already scopes the session. This saves about 150 bytes of the 512-byte budget and makes the Wireshark walkthrough easier.

### 10. LOW-MEDIUM: clients have no liveness check

Only the server pings. A client whose server or Wi-Fi vanished blocks on `recv` indefinitely.

**Fix:** a client treats more than 15 seconds without any TCP frame (PINGs arrive about every 5 seconds) as a disconnect and runs RESUME_SESSION with backoff.

### 11. LOW: complexity without justification

- `room_id` on every request plus ROOM_MISMATCH is redundant, because one connection binds one actor in one room. State that rule and derive the room. Keep `room_id` only if you value it for readable Wireshark captures.
- EDIT_QUESTION in LOBBY duplicates PUT_QUIZ. In FINISHED it changes nothing playable, since the same room can't replay, and the host can edit the local JSON instead. Restrict EDIT_QUESTION to future questions during an active session.
- `accepted: true` in the SUBMIT RESULT is always true. Either drop it or note that it is redundant.

---

### Cross-check of named messages and fields

| Item | Status |
|---|---|
| All 25 message types | Present in the catalogue |
| Error codes | All used codes defined, except the gaps in #8 |
| `actor_id`/`player_id`, `live`, `session_id`, PING/PONG envelope | Undefined or inconsistent (#5) |
| Examples | Match the envelope; LIVE_STATUS changes under #1 and #9 |
| Typical-session diagram | Consistent |
| Edit/reconnect diagram | Missing the UDP rebind; replay note changes under #3 |

### Product policies still unconfirmed

1. Minimum players to start and maximum room capacity (instructor question pending).
2. Removal policy:
   - permanent name reservation
   - whether a removed player's already-accepted answer on the open question is scored
   - whether players removed in LOBBY appear in the final rankings (I suggest deleting them entirely)
3. Voluntary leave. With no LEAVE message, a player who quits stays eligible and blocks early close on every question until the host removes them. Either accept host removal as the remedy or add LEAVE_ROOM as self-removal.
4. Whether EDIT_CANCEL stays paused or restores a prior OPEN.
5. Whether "edit future questions" includes add, delete and reorder. The draft supports replace-in-place only.
6. Whether players see `answered_count`. The agreement only covers the shared display.
7. Host-disconnect detection latency. A silent drop takes about 15–20 seconds to detect, so timer time is consumed before the pause. Document this as accepted.
8. Whether a host-loaded file uploaded over TCP satisfies "questions loaded from a file." I believe it does; state it explicitly in the report.
9. Symbols, numeric limits and room cleanup (already listed as open).

*Vault note:* you asked for no tools or edits, so I did not read `index.md` or the project overview and did not update notes or `log.md`. The RULES.md content was supplied inline.
