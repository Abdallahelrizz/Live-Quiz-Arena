# Claude final review of Fat-Rat v7

2026-11-05. Raw independent response based on working specification and extracted report prose.

# Fat-Rat v7 sections 1–2: final review

There is one real protocol contradiction, one figure problem that matters in a black-and-white document, and several smaller wording fixes.

## High

**1. CLOSE_ROOM after a due timeout cannot succeed as written** (spec §2.1 and §2.5 table; report §2.6 and §2.10)
- **Problem:** The text says CLOSE_ROOM applies any overdue timeout, then discards the unclosed attempt. But the general order (§2.3) is: expire the overdue attempt, then check `expected_control_rev`. Automatic close advances `control_rev`, so the host's revision is now stale. The command returns STALE_STATE, and the "then discard" path can never run. The planned "close versus deadline" test has no defined outcome.
- **Fix:** Use this wording in both documents: *"If the deadline has already passed, the question closes and scores normally, and `control_rev` advances. That CLOSE_ROOM then returns STALE_STATE followed by fresh STATE (RESULTS). The host may confirm closure again."* This keeps the UI warning ("an unclosed question will not score") accurate. Remove "then discard" wherever it follows the timeout clause.

**2. Figure legend does not match arrow styles** (report Figure 1 caption; spec §2.8 Mermaid)
- **Problem:** The caption says "solid arrows are TCP, dashed are UDP." In the Mermaid source, every server-to-client TCP reply uses `-->>` (dashed), and `UDP_HELLO` uses `->>` (solid). In black and white, line style is the only cue, so if the figures were rendered from this source the legend is wrong in both directions.
- **Fix:** Use `->>` for every TCP message and `-->>` only for `UDP_HELLO` and `LIVE_STATUS`, then re-render. Or change the legend to rely on the TCP/UDP label prefixes.

## Medium

**3. FINISHED + CLOSE_ROOM no-op is underspecified** (spec and report CLOSE_ROOM catalogue rows)
- The row reads "no-op … RESULT {} then STATE." This conflicts with "successful no-ops send only their RESULT."
- It is also unstated whether the no-op check comes before or after `expected_control_rev`, and whether the no-op is cached. REMOVE_PLAYER's no-op is explicitly uncached.
- **Fix:** *"For all in-room host mutations, `expected_control_rev` is checked before no-op detection. FINISHED + CLOSE_ROOM returns RESULT {} only: no STATE, no revision change, no cache entry."*

**4. Lost CREATE_ROOM reply conflicts with the retry rule** (spec §2.4 bullet 1; report §2.9)
- **Problem:** "Never blindly retry; resume and reconcile first" is impossible after a lost creation reply, because the host has no `room_id` to resume.
- **Fix:** Add an explicit exception: *"After an uncertain CREATE_ROOM, open a new connection and resend the identical request. ROOM_ALREADY_CREATED or HOST_HAS_ROOM leads to RESUME_SESSION with existing_room_id. A fresh request_id is also safe and yields HOST_HAS_ROOM."*

**5. Invalid `host_token` handling is undefined** (spec §2.1 and §2.2; report §2.2 and §2.10)
- **Fix:**
  - A token must be exactly 43 characters from `[A-Za-z0-9_-]` and decode to 32 bytes.
  - A malformed token in CREATE_ROOM or RESUME_SESSION returns INVALID_MESSAGE. It is non-fatal and the token is never echoed.
  - A well-formed token that is not this room's HOST or player token returns the generic INVALID_TOKEN.
  - The digest is computed over the decoded bytes.
  - Report §2.2's list of non-UUID values ("except the PIN, reconnect token and UDP nonce") should also name `host_token`.

## Low

6. **CREATE_ROOM validation order:** place the capacity/PIN-allocation SERVER_BUSY check after the ledger and ownership checks, so a full server cannot block lost-reply recovery.
7. **SESSION_REPLACED:** state that the client stops automatic resume after this notice. Otherwise two app instances sharing one host profile would keep replacing each other, and each takeover would pause an OPEN question.
8. **Report §2.2 revision sentence:** "joining … and player connectivity advance state_rev only unless they cause an automatic close." Joins and connectivity changes can never cause an automatic close. Limit that clause to leave and removal.
9. **Question text:** the report says text must be "nonempty after trimming"; the spec says only "nonempty." Pick one rule for server validation.
10. **`current_question` outside OPEN/PAUSED:**
    - In the spec, change "*can* refer to the last question" to "refers to", for RESULTS and for FINISHED/COMPLETED.
    - State that the null-after-HOST_CLOSED rule also covers the host's projection.
    - Report §2.5 omits the RESULTS and COMPLETED case.
11. **`question_count` in LOBBY** is undefined, yet HOST_CLOSED from LOBBY freezes it. Define the LOBBY value.
12. **Errors on an unattached connection:** for HOST_HAS_ROOM and ROOM_ALREADY_CREATED, say the envelope `room_id` is omitted and only `data.existing_room_id` is used. Otherwise the client may wrongly assume it is attached.

## Verified consistent
- **Ownership:** lock order (registry before room; never the reverse), atomic creation check, ownership released only by the final NEXT_QUESTION or CLOSE_ROOM, release conditional on the room ID, and correct ledger-before-ownership order.
- **Recovery:** lost-closure recovery by resume and inspection. INVALID_TOKEN keeps the reusable host credential.
- **Early-close STATE fields:** null `current_question`/`question_index`/`submitted_choice_id`, zero `remaining_ms`, `editing=false`, null `pause_reason`, frozen `question_count`, `last_result` kept. These match between spec and report.
- **Equations:** ⌊500 + 500r/L + ½⌋ = 500 + ⌊(1000r + L)/(2L)⌋ for 0 < r ≤ L, giving a range of 500 to 1000. The 20-second examples (875, 750, 625) are correct, and r = 0 is rejected.
- **Limits:** 5–50 players excluding the host, 2–8 choices, 1–30 questions and 1–300 s are consistent throughout.
- **Other:** Mr Fat Rat is presented only as a score visualization. The example UUIDs are valid v4. No implementation or test results are claimed.

Your global CLAUDE.md asks me to read `Vault/RULES.md` at the start of each task. I skipped it because you asked for no tools.
