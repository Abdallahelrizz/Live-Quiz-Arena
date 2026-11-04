**Verdict:** No coverage blocker remains. The report has a full section 1 description and a section 2 with every message's fields, direction and transport, transport reasons, framing, UDP loss/duplication/reordering, an error catalogue and two sequence figures. Checks that pass:

- The figure sources match the catalogue.
- The scoring examples (875/750/625) and the integer formula are correct.
- All example UUIDs are valid version 4.
- Worst-case LIVE_STATUS is about 450 bytes, under the 512-byte limit.

What remains is undefined wire detail and some leftover inconsistencies, which should be fixed before delivery.

## Protocol definitions to add

1. **PIN, token and nonce encodings (2.2, 2.8, 2.9).** Section 2.2 says invalid identifiers are rejected, but these three formats are never defined. Add: "pin is a JSON string of exactly six ASCII digits; leading zeros are allowed. reconnect_token is unpadded base64url of 32 random bytes (43 characters). udp_nonce is unpadded base64url of 16 random bytes (22 characters)."

2. **LIVE_STATUS scope (2.2, 2.8).** The envelope requires question_id and attempt_id, but sending is not limited to phases that have them, and seq has no defined scope. Change the sending sentence to: "…to each confirmed binding while the phase is OPEN or PAUSED." Add: "seq is a per-binding counter starting at 1; a new binding resets the client's last accepted seq."

3. **pause_reason after editing ends (2.4, 2.5).** EDIT_COMMIT and EDIT_CANCEL leave the room PAUSED with editing false, but no pause_reason is defined for that state. Add: "After EDIT_COMMIT or EDIT_CANCEL, pause_reason becomes MANUAL."

4. **Revision effect of joins and removal (2.2).** The rule only covers leaving, yet the REMOVE_PLAYER no-op text implies that a real removal changes revisions. Replace the leaving sentences with: "Joining, leaving, removal and connectivity advance only state_rev. control_rev advances for quiz content, phase, pause and editing changes, including an automatic close caused by a departure."
   - In 2.9, add after "…expected_control_rev is stale": "; an evicted REMOVE_PLAYER replay is a harmless no-op."

5. **ERROR data schema (2.4 ERROR row, 2.9).** The row lists only current_control_rev. Replace with: "Optional fields: current_control_rev (STALE_STATE), reason (SESSION_REVOKED), question_id/attempt_id/choice_id (ALREADY_ANSWERED), retry_after_ms (RATE_LIMITED, SERVER_BUSY)."
   - Also state whether STALE_ATTEMPT pushes a fresh STATE, as STALE_STATE does, or requires the client to send GET_STATE.

6. **Username validation (1.2, 2.3, 2.9).** There is no minimum length, normalization form or rejection code. Use: "1 to 32 code points after NFKC normalization and trimming; duplicates are compared using casefold(). The same rule applies to the CREATE_ROOM username."
   - Add INVALID_USERNAME to the INVALID_PIN / NAME_TAKEN row.

7. **Question constraints (2.3).** Section 2 never restates what INVALID_QUIZ enforces. Add: "choices has 2 to 10 entries; correct_choice_id equals one of its choice_ids; time_limit_ms is an integer from 1,000 to 300,000; texts are nonempty after trimming."

8. **Request-ID scope (2.9).** The exact-retry rule matters only across reconnects, but it is not said whether fingerprints survive a new session. Add: "Answer and host-mutation fingerprints are keyed by (actor_id, request_id) and survive session replacement."

## Leftover and mismatched text

9. **2.9, INVALID_TOKEN / SESSION_REVOKED row.** It states the unknown-token rule twice. Replace with: "Unknown room or token, including after a server restart: INVALID_TOKEN with a generic message. Retained revoked token: SESSION_REVOKED with data.reason HOST_REMOVED or LEFT_ROOM. Neither restores identity or issues a replacement token."

10. **1.5.** "A host who never returns leaves the room paused" is only true if a question was open. Replace with: "…leaves the room in its current phase, paused if a question was open."

11. **ADD_QUESTION row.** "Insert a fresh question" conflicts with 2.7, which allows re-adding a deleted question with its saved ID. Replace with: "Insert a question whose ID is not already in the quiz."

12. **2.7.** With "byte-identical", differences in key order or whitespace would defeat the no-op rule. Replace with "identical after validation (equal canonical compact JSON)."

13. **Check the rendered DOCX:**
    - the table header "Directionand transport";
    - the cells reading "H to STCP" (they should read "H to S" with "TCP" on its own line);
    - the heading, which should read "2.9 Errors, retries and session recovery".

Everything else, including the reserved name, the frozen final leaderboard, leave/removal semantics, orphan handling and the pending capacity and start limits, is internally consistent and needs no change.
