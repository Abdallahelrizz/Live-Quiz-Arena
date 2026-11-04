# Claude review of Fat-Rat team limits

2026-11-04. Raw response; review only, no implementation validation.

Per your no-tools instruction I did not open `~/Documents/Vault/RULES.md`, which your global CLAUDE.md normally asks me to read first. This review is based only on the pasted text.

## Defects

1. **2.1 wording states a false invariant.** "Each room has one noncompeting host and 5-50 players" is not true in LOBBY, where the room has 0–4 players before start. It is also not true after departures, where the count can drop to 0. Suggested rewording: "At most 50 ACTIVE players at any time; START_QUIZ requires at least five connected ACTIVE players."

2. **The byte-budget check misses growth it cannot refuse.** The 1 MiB check runs only on joins and quiz mutations. Leave, removal and disconnect can still enlarge host STATE:
   - `participation` changes from `ACTIVE` to `REMOVED` (+3 bytes per row).
   - `connected` changes from `true` to `false` (+1 byte per row).
   - `pause_reason` and `live` can change from null to non-null.

   None of these can be rejected, so a room admitted near the edge could later produce STATE above 1 MiB, which receivers close as fatal. Fix: state that the conservative estimate assumes the worst-case width of every variable field, not just score and rank.

3. **Retained history is bounded only by the budget.** LEFT/REMOVED rows free a slot but are kept until shutdown, with no pruning or room release. Repeated join/leave cycles, or join plus host removal, can therefore drive a room to permanent `SERVER_BUSY` (retryable=false) with fewer than 50 ACTIVE players. Worst-case escaped names and a maximal quiz leave room for roughly several hundred retained identities, so 50 ACTIVE players always fits on its own. The effective cap is therefore min(50, budget). This is a team decision. At minimum, document it as a known limitation and say what `SERVER_BUSY` on join means to the user.

4. **The one-unfinished-room rule has no protocol presence and conflicts with stated retention.** This is not a proposed resolution:
   - The CREATE_ROOM row and the error table have no check or code for it.
   - 2.4 says orphaned host rooms persist until shutdown, and 2.7 retains abandoned paused rooms the same way.
   - There is no host leave or close operation.
   - A LOBBY room that never reaches five players is unstartable and stays "unfinished" forever.

   Under any future enforcement, these would permanently block a host. Mark CREATE_ROOM and the 2.4/2.7 retention text as pending the host-identity and room-release decision so the catalogue does not read as complete. Similarly, "noncompeting host" is enforced only per actor: the host actor cannot submit. Nothing stops the same person from joining as a player from another client. Do not claim person-level enforcement until that open item is settled.

5. **Error precedence is unspecified.**
   - START_QUIZ with no quiz and fewer than five players could return either INVALID_STATE or NOT_ENOUGH_PLAYERS.
   - JOIN_ROOM can fail as FINISHED, INVALID_USERNAME, NAME_TAKEN, ROOM_FULL or SERVER_BUSY, with no defined order.

   Fix the order. For example, for START: phase → quiz → player count. For JOIN: PIN → FINISHED → name validity → NAME_TAKEN → ROOM_FULL → budget.

6. **The 2.9 verification plan omits the new limits.** Add tests for:
   - The start gate with 4 vs 5 connected players, where disconnected players do not count.
   - ROOM_FULL at 50 including disconnected players.
   - Concurrent joins at 49.
   - A slot freed by leave/removal, with the history row retained.
   - Resume at 50 succeeding.
   - 2/8/9 choices and 30/31 questions.
   - The budget boundary with heavy departure history.
   - A 50-player load run.

7. **The status line implies prior reviews covered the v6 team limits.** They did not. Record this review in protocol-review.md.

8. **Rat feeding (low risk).** It is not mentioned, so there is no conflict. If the shared screen animates per-player points for each question, host STATE does not carry that data: host `own_result` is null, and the leaderboard only holds cumulative scores. Diffing leaderboards would break across a host reconnect. Either derive the visual from cumulative `total_score`, or route any new field through the catalogue and the budget. The app name Fat-Rat also does not appear anywhere. That is fine if it is introduced elsewhere in the report.

## Checked and consistent

- **Numeric limits:** No stale values remain. Choices are 2..8 everywhere, `symbol_index` is 0..7 with eight cheese symbols, questions are 1..30 (with QUESTION_LIMIT and delete-last handling), and the host is excluded from all counts.
- **Count semantics:**
  - The start gate applies at START_QUIZ only.
  - The game continues below five players, including with zero eligible players, thanks to the nonempty all-answered rule.
  - Disconnected and orphaned identities hold slots but do not satisfy the start gate.
  - Resume is reattachment and consumes no slot.
  - LEFT/REMOVED players can never become ACTIVE again, so the ACTIVE count grows only through locked joins.
- **Sizes:** LIVE_STATUS stays under 512 bytes even with maximum values.
- **Scoring:** The maximum score of 30,000 is consistent with 30 questions.
- **Open item:** Host-identity enforcement remains marked open, and no approval is invented.
