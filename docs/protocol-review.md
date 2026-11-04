# Protocol review record

For the current completed-versus-outstanding checklist, see [status-and-next-steps.md](status-and-next-steps.md). These are design/document reviews; no application, capacity or LAN test has run.

Date: 2026-11-05. Scope: [section 2 working specification](section-2-protocol.md), checked against the course PDF and [agreed gameplay](gameplay-decisions.md).

Status: six Claude reviews completed across the design/report revisions, including the v7 host policy and actual report text. Codex assessed findings and applied final specification/report clarifications. Document visual QA is recorded below; no application implementation validation is claimed.

## Review method

1. Codex drafted the protocol independently from the user's accepted decisions and the official assignment.
2. Claude received the full assignment text, agreed/open decisions, vault rules and v1 draft, with tools disabled. It was asked to challenge fields, phase transitions, timing, retry behavior, UDP ordering and information disclosure.
3. Codex evaluated findings, revised the draft and sent the changed specification/dispositions and actual report text back to Claude for further review.
4. Any remaining product choices will stay explicitly proposed. Design review cannot establish that a future implementation works.

## Codex document checks on v1

- Three JSON examples parse successfully; sample answer request/response and UDP snapshot match catalogue names.
- LIVE_STATUS with full-length UUIDs encodes to 377 bytes, below the proposed 512-byte limit for that example. This is not a proof for every allowed numeric value.
- Markdown fences balance and local links to gameplay/source PDF resolve.
- Traceability table locates each requested section 2 component. Diagrams are Mermaid source, not rendered figures or captured network traffic.

## Codex findings to address alongside Claude review

- Define host current_question serialization explicitly; the draft described player projection more precisely.
- Exempt recovery traffic from exhausted mutation outcome history, or a full replay cache could prevent GET_STATE/reconnection recovery.
- Bound outgoing STATE size as well as incoming quiz requests. An upload that fits one frame can still make a larger host snapshot overflow after adding roster and envelope data.
- Keep the current/last attempt question definition immutable separately from the reusable quiz. Post-session edits must not change selection IDs associated with historical results.
- Make PING schedule and UUID/id aliases explicit; distinguish request envelopes from PONG session events.

## First Claude review and Codex dispositions

Raw response: [Claude review of v1](reviews/claude-v1.md). Claude found 11 groups of issues and did not approve v1. Codex evaluated the suggested changes rather than accepting all of them.

| Finding | Disposition in v2 |
|---|---|
| One revision conflates membership ordering and host control epochs | Split state_rev from control_rev. Retain control_rev in UDP and expected_control_rev in host commands. **Rejected** phase-only UDP matching: OPEN → PAUSED → OPEN would admit an old OPEN packet. Also **rejected** removing command preconditions: a delayed PAUSE can otherwise execute after a full pause/resume cycle. |
| Late answers hit phase error before deadline error | Matching closed attempt explicitly returns ANSWER_LATE; older attempt returns STALE_ATTEMPT. |
| Replay cache loses follow-up STATE, caches transient errors and eventually locks out recovery | Never cache read/recovery/identity operations or errors. Retain accepted-answer records and only last 32 successful host results; replay includes fresh STATE. Evicted host commands fail old control revision. **Rejected** removing all retry guards; commands can recur after phase cycles. |
| Pause/edit/disconnected status ambiguous | Keep editing with a strict invariant (true iff reason EDITING) and independent host_connected. Commit/cancel leave MANUAL pause; resume rechecks all-answered. |
| STATE and ID schema holes | Per-recipient snapshots, always-present nullable live, explicit host question projection, zero-score initial rows, UUID ownership/actor aliases, new session_id per TCP attachment, explicit PING/PONG envelope. |
| Deadline/rounding underspecified | Integer monotonic milliseconds, deadline is exclusive, exact half-up integer score formula and eligible-only live answer counts. |
| Outgoing STATE can exceed incoming frame cap | Compact UTF-8, separate 512 KiB quiz cap and conservative complete outgoing-state/result byte budget before accepting quiz/join. A quiz cap alone would not bound roster overhead. |
| Missing roles/errors/fatality | Explicit fatal vs recoverable parse/schema failures, NOT_PLAYER/ALREADY_ATTACHED/UNKNOWN_PLAYER, retryability and late/duplicate error data. |
| UDP demo address/rebind/staleness gaps | Explicit IPv4 and same address for TCP/UDP, rebind after reconnect shown in diagram, apply only after BOUND, stale polling only in active phases. **Rejected** stripping room/attempt/control identifiers: explicit fields aid isolation/debugging and the checked example fits 512 bytes. |
| Client can wait forever after server disappears | Client-side complete-frame inactivity timeout and reconnect backoff. |
| Complexity/overlapping quiz operations | Retain readable room IDs for Wireshark and defensive checks. Retain post-FINISHED quiz edits to honor user preference, but freeze historical attempt definitions. Record replace-in-place scope and remaining UI/product questions. |

Additional Codex findings listed above are addressed in the same revision: recovery exemptions, host serialization, byte budgets, immutable historical attempts, ID definitions and ping cadence.

## Remaining product choices and verification limits

- Player minimum/capacity await instructor input. GUI/toolkit/symbol appearance remain open.
- Abdallah subsequently approved removal/leave score retention and token revocation, cancel-stays-paused, local token persistence, retain-until-shutdown, fresh-room replay, 1-300 second timers, up to 100 questions and 32-character usernames. Empty-eligible-set behavior and transport resource limits remain engineering defaults.
- Abdallah approved voluntary leave and adding/deleting/reordering future questions. V4 adds LEAVE_ROOM, ADD_QUESTION, DELETE_QUESTION and REORDER_QUESTIONS and preserves locked current/completed prefix.
- Only design/document checks have run. No network implementation, load test, second-device LAN run, screenshot or Wireshark evidence exists yet.
- The Mermaid diagrams still require rendering during final report preparation.

## Second review

Raw response: [Claude review of v2](reviews/claude-v2.md). Claude found no remaining correctness issue requiring redesign, with one material recipient ambiguity and five groups of clarifications. It explicitly withdrew its phase-only UDP matching suggestion after checking the pause/resume case and accepted expected_control_rev plus the bounded successful-host cache.

Codex reviewed and resolved these in v3:

| Finding | Final disposition |
|---|---|
| STATE recipients not explicit enough for exact UDP control revision matching | All control_rev/host connection changes and roster join/removal changes notify every attached session with private projections; player-only connectivity changes notify host/reconnecting player. Diagram annotated. |
| Host replacement can outrun server disconnect detection | Proposed safe default applies disconnect/pause transition before replacing a still-attached host, preventing automatic continuation after a blind interval. |
| Ticker can mix fields across transitions; UDP source interface ambiguous | Read one committed snapshot under room lock and send after release; OPEN uses deadline, PAUSED stored duration. Server binds UDP to advertised IPv4; local/remote clients use that same address. |
| Edit can grow quiz beyond size budget | Recheck quiz and worst-case complete outgoing-state/result budget on every quiz mutation, including frozen-attempt/result overhead. |
| Edge-case errors and wording | Define current/future/completed edit errors and permanent byte-budget refusal. Retain STALE_ATTEMPT for a previous question after advancing, consistent with attempt scoping; ANSWER_LATE for the matching closed attempt. Replace ordinary PAUSED with PAUSED not editing. |
| Proposed policies read as settled | Inline labels for empty-eligibility early close, retained name reservation, and finished-room/new-room replay policies. |

Codex also tightened replay/connection-generation checks to execute inside the same room synchronization and bounded numeric wire values. These final clarifications were checked by Codex after Claude's second response; no third Claude approval or formal proof is claimed.

## Final document checks

- Three JSON examples parse; obsolete expected_rev and UDP state_rev fields are absent.
- With UUIDs and 64-bit numeric stress values, the sample LIVE_STATUS is 464 UTF-8 bytes, within the proposed 512-byte cap.
- Exact integer scoring matches 1000/875/750/625 examples, mathematical zero-time endpoint, and half-point rounding. Zero-time new answers are rejected by the deadline rule.
- Markdown fences and local links checked; source diagrams retained for later PDF rendering.
- Requirements cross-reference the message catalogue, transport reasons, framing, error/recovery handling and the sequence diagrams.

This establishes a reviewed working design, not implementation correctness, instructor approval or a submission-ready PDF.


## Approved scope extension: focused review

Abdallah approved all recommended product policies after the v3 review, and requested a removal notification. V4 records that the notification is a message while authorization is enforced by revoking the existing token. First review attempt was interrupted with no response; retry returned the completed [focused Claude review](reviews/claude-v4.md). Claude reported no structural blockers but identified material inconsistencies; Codex resolved these in v5:

| Finding | Disposition |
|---|---|
| Frozen QuestionResult leaderboard can conflict with current STATE ranking | Removed duplicate leaderboard from QuestionResult. STATE.leaderboard is authoritative; The complete FINISHED ranking, including participation labels, is frozen; later departures change live authorization only. Departure phases explicitly listed. |
| LEAVE_ROOM needlessly invalidates host commands | Leave bumps state_rev/roster only, not control_rev unless it causes an automatic close. Host commands remain guarded against actual control changes. |
| Removing LEFT identity overwrites the departure reason needed for recovery | First departure cause is permanent; removing LEFT/REMOVED identity is a successful no-op. SESSION_REVOKED requires HOST_REMOVED/LEFT_ROOM reason (Codex had already tightened optional wording). |
| Saved token for a room lost on server restart retries forever | Unknown room/token gives generic INVALID_TOKEN; client stops and discards entry on definitive INVALID_TOKEN/SESSION_REVOKED, retains token on connection failures alone. |
| Unacknowledged leave, current-edit discard, PAUSED eligibility | Successful resume means leave did not commit and allows a new leave request under existing confirmation. Restart discards all submissions including departed players. Eligible set defined in OPEN and PAUSED. |
| Repeated future mutations and phase-specific errors | Validated canonical-identical reusable edit/reorder is a no-op without revision changes; EDIT_COMMIT remains a restart even with identical text; explicit current/completed lock errors and exact permutation rule. |
| Host excluded by player ACTIVE terminology; implicit command range too broad | Explicit host inclusion in recipients and an exact list of privileged commands. All phase tables cover new operations. |
| Notification terminology and product authority | Agreed access revocation/notification is implemented as REMOVED plus definitive token-denial response, not a new token. Codex recorded this as an engineering interpretation of the user request; no extra confirmation is required for that routine protocol representation. Finished-room new-join rejection remains an engineering default, separate from approved fresh-room replay. |

Codex checked and incorporated the review rather than accepting it as proof. The actual Word-report text derived from v5 was subsequently sent through the fourth Claude review recorded below. Catalogue now contains 31 message types and three Mermaid sequence diagrams. Document checks cover JSON, local links, unique catalogue entries and the approved decision labels; application/network tests remain pending.

## Word report review and delivery checks

Raw fourth response: [Claude review of actual report text](reviews/claude-report.md). Claude found no required-content coverage blocker. It confirmed complete message fields/directions/transports, justifications, framing, UDP loss/duplication/reordering, error catalogue, two sequence figures, scoring arithmetic and valid example UUIDs. Codex resolved its remaining details:

- Explicit six-ASCII-digit JSON PIN with leading zeros; unpadded base64url 32-byte reconnect token (43 characters). UDP nonce uses 32 lowercase hex characters for 128 bits; retained this valid encoding rather than the reviewer's alternative base64url.
- LIVE_STATUS only for confirmed bindings in OPEN/PAUSED; per-binding sequence starts at 1 and client resets last accepted to 0.
- MANUAL pause reason after edit commit/cancel, already defined in the working specification and now explicit in the report/state rules.
- Joins/leave/connectivity change state_rev only, unless an automatic close occurs. Real host REMOVE_PLAYER remains an explicit host mutation advancing control_rev as specified; did not adopt the unnecessary control-revision redesign. No-op removal preserves status/reason and revisions.
- Explicit ERROR extension fields and fresh STATE after both stale-revision/attempt errors. Invalid names use INVALID_USERNAME; keep the agreed NFC normalization rather than introducing the reviewer's NFKC alternative.
- Restated valid question choices/correct ID/time/nonempty text constraints; fingerprints keyed by actor/request survive TCP attachment replacement.
- Removed duplicate token-error wording, corrected abandoned-host phase wording, allowed re-adding a deleted future ID, and defined canonical validated equality for reusable no-op edits while preserving current-attempt restart semantics.

Report created with bundled python-docx, rendered with bundled LibreOffice through render_docx.py, and all final page PNGs inspected. The report is a design document: code, network tests and Wireshark evidence remain future work. Player start minimum/maximum remain explicitly pending Abdallah's instructor update. Final artifacts are in output/docx; rendered PDFs/PNGs are internal QA only.

## Fat-Rat team revision 2026-11-04

The team renamed the application Fat-Rat and selected 5-50 players excluding the host, 2-8 cheese-themed choices and 1-30 questions per quiz. These supersede earlier limits and pending-capacity statements above. One host may own one unfinished room; multiple rooms require different hosts. Codex updated the working specification and report source. Proposed count semantics: five connected ACTIVE identities at initial start; 50 ACTIVE identities including disconnected reconnectable players; permanent departures release active slots, retained historical rows remain size-budgeted. Falling below five after start does not stop the game. Host identity and room-release enforcement await clarification. The earlier four Claude reviews cover v5, not these new v6 changes. Rat feeding is a proposed visual reward, not a second scoring system. Functional/capacity testing belongs to Section 4; nothing has been implemented or capacity-tested.

### Fifth focused review and Codex disposition

Raw response: [claude-fat-rat-limits.md](reviews/claude-fat-rat-limits.md). Fixed wording that incorrectly implied five players must always be present; strengthened worst-case message-size estimates for unavoidable departures/connectivity changes; documented retained-history budget refusal below the active-player cap; added deterministic start/join error precedence and v6 boundary/load checks. Cross-room host identity and room-release remain explicitly pending, including CREATE_ROOM catalogue completeness. Prior reviews are now explicitly scoped to v5. Rat visuals should derive from cumulative server-confirmed score so reconnect does not replay feeding; still a proposal. Restored the supplied course source title in the bibliography while retaining Fat-Rat as the application name. The updated Word draft is 14 pages and all pages were visually inspected; host-rule details still prevent calling this final.

## Host policy confirmed 2026-11-05

Abdallah selected saved host identity on the device without accounts, and confirmed one unfinished room per host, successive rooms after finishing/closing the current room, and separate hosts for simultaneous server rooms. This settles product policy; detailed credentials, close-room messages and synchronization still need integration into the protocol and Word report. No code or revised Word artifact was produced in this confirmation turn.

## Completed v7 integration and sixth review 2026-11-05

The saved-device host policy is integrated into the working specification and report: CREATE_ROOM carries host_token; a per-host successful creation ledger supports lost-reply recovery without duplicate rooms; CLOSE_ROOM and finish_reason define early/natural termination; registry-before-room synchronization and conditional release preserve single-room ownership. The report contains 32 message types, the Fat-Rat limits/theme and two native editable scoring equations. All text and borders are black with white backgrounds, including legacy styles and embedded diagrams. These remain design documents; application code and network tests are not built.

Raw review: [claude-fat-rat-final.md](reviews/claude-fat-rat-final.md). Codex assessed and resolved the findings:

- Due timeout is applied before revision checking; it scores normally and makes an old CLOSE_ROOM return STALE_STATE. Reconcile before issuing a new close under the existing confirmation. Successful early termination discards only a still-unclosed attempt.
- Revision checks precede FINISHED-close no-op detection; no-op returns RESULT only with no revisions/cache entry.
- Persist pending creation request ID/username before send; recover an uncertain creation using its identical request. The review's suggestion that a fresh creation ID is universally safe was rejected: the original room might have finished meanwhile, allowing unintended new creation.
- Canonical 43-character create/resume tokens decode to exactly 32 bytes and re-encode identically; malformed returns nonfatal INVALID_MESSAGE, a well-formed mismatch INVALID_TOKEN. Host index uses SHA-256 of decoded bytes. New allocation resource checks follow creation-ledger/ownership recovery checks. Ownership-error sockets remain unattached and omit envelope room_id.
- SESSION_REPLACED stops automatic recovery until explicit user action. Clarified joining/connectivity revision wording, trimmed nonempty text validation, LOBBY question_count and both-role current_question after early closure.
- Corrected Mermaid source arrow styles to agree with TCP-solid/UDP-dashed legend. Report figures already used the correct line styles; they are generated independently by the builder.
- Equations and revised limits were independently checked. Document rendering and structural/monochrome verification are separate from unperformed application tests.

Earlier pending-policy statements in this review log describe historical drafts and are superseded by this v7 entry.
