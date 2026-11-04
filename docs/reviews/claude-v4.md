# Focused review of v4 (future-question operations and departures)

**Verdict:** No redesign blocker. Four medium findings should be fixed before you freeze v4. The rest are low-severity clarifications.

These held up under review: revoke-before-notify ordering, generation checks on obsolete attachments, replay lookup before the revision check, the all-answered recheck on RESUME_QUIZ after a paused departure, the prefix boundary including the just-closed question in RESULTS, NEXT reading the latest suffix, departed answers being scored but not counted live, and SESSION_REVOKED with a reason enforcing removal when REMOVED is lost.

## Medium

**M1. The abandoned-join sentence is out of date, and an orphaned identity blocks early close.**
2.4 says "host removal/server cleanup handles the abandoned reservation". That is no longer true:
- Removal keeps the name reserved.
- Rooms now persist until server shutdown, so there is no cleanup.

There is also an unstated consequence. A lost first JOIN RESULT, or a crash before the token is saved, leaves an ACTIVE, disconnected, eligible identity. Every later question then runs to its timer and never closes ALL_ANSWERED.

Fix, in 2.4: "A lost first identity response cannot be recovered by username. The orphaned identity stays ACTIVE and eligible, which prevents ALL_ANSWERED early close, until the host removes it. Its name stays reserved until server shutdown, so the user rejoins under a different name."

Fix, in 2.2: change "until room cleanup" to "until server shutdown".

**M2. Confirm how removal treats the name.**
Your brief says removal "revokes token/reserved name". `gameplay-decisions.md`, 2.2 and 2.7 all say the name *remains* reserved. Read literally, these conflict.
- Confirm with Abdallah.
- If the name is released on removal, change the agreements bullet, 2.2 and 2.7 together. M1's wording changes too.
- Keeping the reservation is what is recorded and is safer against impersonation.

**M3. LEAVE_ROOM should not increment control_rev, and removing a LEFT player should be a no-op.**

*control_rev:* 2.2 lists "a committed LEAVE_ROOM" as a control_rev trigger. Any player leaving therefore makes the host's in-flight PAUSE/NEXT/EDIT/START fail with STALE_STATE. That is exactly the "player churn invalidating host commands" the same paragraph says the design avoids. Joins also change eligibility and do not increment it. Nothing needs the increment: an ALL_ANSWERED close caused by a leave is already an "automatic question close".
- Fix: delete "a committed LEAVE_ROOM" from the control_rev trigger list.
- Leave increments state_rev only. The existing join/removal/leave recipient rule already sends STATE.

*REMOVE on LEFT:* The error table says removing a LEFT player "changes their status to REMOVED". This has two problems:
- It rewrites a voluntary departure in history.
- If the leaver's final RESULT was lost, their resume now returns HOST_REMOVED instead of LEFT_ROOM. That defeats the 2.7 recovery path ("identify that leave committed").

Fix: "REMOVE_PLAYER on a REMOVED or LEFT player is a successful no-op: RESULT only, no revision change, participation unchanged." Move this sentence out of the ALREADY_ATTACHED/UNKNOWN_PLAYER row and into the REMOVE_PLAYER catalogue entry.

**M4. The two leaderboards diverge, and the "frozen" FINISHED ranking can still change.**
STATE carries both `leaderboard` (live roster and participation) and `last_result.leaderboard` (frozen at close).
- A join, leave or removal during RESULTS makes them disagree. The new zero-score row is missing from one, and participation shows ACTIVE in one and LEFT/REMOVED in the other.
- In FINISHED, 2.5 calls scores and history frozen. LEAVE_ROOM and REMOVE_PLAYER have no phase restriction, though, so final-ranking participation can still change. A late join during final RESULTS also leaves the two rankings different at FINISHED.

Fix:
- (a) Remove `leaderboard` from QuestionResult. STATE.leaderboard becomes the single ranking, which also cuts the duplicated-field byte budget.
- (b) Add: "On transition to FINISHED, freeze the final leaderboard including participation. In FINISHED, LEAVE_ROOM and REMOVE_PLAYER only revoke the token and detach; they do not alter the final leaderboard."
- Alternative to (b): reject both in FINISHED with INVALID_STATE. Choose one and state it.

## Low

**L5. Eligibility wording.**
2.4 says "In OPEN, eligible players include…". Change it to "In OPEN/PAUSED". LIVE_STATUS is also sent in PAUSED, and joins during PAUSED must count as eligible.

**L6. Host sending LEAVE_ROOM.**
Specify that it returns NOT_PLAYER. That error row currently mentions only a host submitting an answer.

**L7. Leaving across an uncertain connection.**
2.4 forbids blind retry, but the leave case is unstated. Add:
- "If RESUME_SESSION succeeds, the leave did not commit. The client may send a fresh LEAVE_ROOM, because the user's confirmation still stands."
- "The client must not show 'left' or discard the token until RESULT `{left:true}` or SESSION_REVOKED arrives."

**L8. Discarding the stored token.**
- Specify that RESUME_SESSION for an unknown room_id returns INVALID_TOKEN, for example after a server restart.
- Have the client discard the stored token on INVALID_TOKEN as well. Otherwise the 1→15 s resume backoff retries a dead room indefinitely.

**L9. No-op list mutations.**
Two sentences conflict:
- "Every successful list mutation increments control_rev."
- "Successful no-op commands send only their RESULT."

Neither says which applies to an identity REORDER, an empty-suffix REORDER after the last question, or an EDIT_QUESTION with identical content.

Fix: "A mutation that leaves the committed quiz byte-identical is a successful no-op: RESULT only, no revision change." This is safe for replay because a no-op is idempotent under the same expected_control_rev.

**L10. Fresh-ID rule.**
"Not used earlier in this room, including deleted IDs" blocks undoing a delete by re-adding the question from the saved JSON. Yet PUT_QUIZ in LOBBY may reload the same IDs. Historical results and answers are keyed by `attempt_id` snapshots, and stale host intent is already caught by expected_control_rev, so the extra rule protects nothing.

Fix: "Question IDs must be unique within the candidate quiz." The completed prefix stays in the list, so this still prevents collisions with played questions during a session.

**L11. Unit for the approved 32-character limit.**
Define it, for example as "1..32 Unicode code points after NFC normalization and trimming". This keeps client-side and server-side validation in agreement. The same applies to the 1000/300/100-character text limits.

**L12. Roster growth against the open capacity question.**
Departed rows are permanent and count toward the outgoing-STATE byte check. Repeated join-and-leave under new names will eventually make joins fail with SERVER_BUSY (`retryable=false`). That is acceptable on a LAN. When the instructor gives a capacity, record whether it counts all retained participants or only ACTIVE ones.

## Superseded or inconsistent sentences to edit

| Location | Current text | Replace with |
|---|---|---|
| 2.4 | "host removal/server cleanup handles the abandoned reservation" | M1 wording |
| 2.2 | "until room cleanup" | "until server shutdown" |
| 2.2 control_rev list | includes "a committed LEAVE_ROOM" | remove (M3) |
| Error table, UNKNOWN_PLAYER row | "removing a LEFT player changes their status to REMOVED…" | no-op rule, moved to REMOVE_PLAYER (M3) |
| 2.2 RankRow / QuestionResult | QuestionResult includes `leaderboard`; "All participants… retain final history" | single leaderboard; frozen at FINISHED (M4) |
| 2.4 eligibility | "In OPEN" | "In OPEN/PAUSED" (L5) |
| 2.5 Future-question mutations | "fresh question ID not used earlier in this room, including deleted IDs" | uniqueness within candidate quiz (L10) |

I made no file or vault edits, as instructed. If Codex applies these, the review record should list M1–M4 and their dispositions.
