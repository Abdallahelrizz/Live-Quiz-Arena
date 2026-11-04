# Fat-Rat: agreed gameplay and open choices

Updated: 2026-11-05. Source: Abdallah's decisions in this conversation and the course requirements PDF. These are planned behaviors, not implemented features.

Current verification status and unfinished work are tracked in [status-and-next-steps.md](status-and-next-steps.md). Application tests have not run; report review and document checks are complete.

## Agreed with Abdallah

- Report sections 1 (Application Description) and 2 (Application-Layer Protocol) are due 2026-11-06. This deadline/scope was confirmed by Abdallah; the PDF itself does not define staged deadlines.
- A noncompeting host creates, saves, loads, edits and manages quizzes through the app. JSON is the storage format; manually authoring JSON is not required. Spreadsheet import was not agreed as required functionality.
- Players join a room with a PIN and username. Invalid PINs and duplicate room usernames are rejected.
- Questions have 2-8 choices, exactly one correct answer, and a host-selected time limit.
- The shared display shows question/answer text and corresponding selection symbols. Player devices show selection symbols, not question/answer text, and show the player's own score. The symbols use a cheese theme; eight distinguishable icon designs remain to be selected.
- Countdown appears on both host/shared display and player devices. The shared display shows the number of submitted answers, not individual choices before closing.
- First valid server-accepted answer is final for that question attempt. Server rejects invalid, duplicate and late answers.
- Correct answers score round(500 + 500 * remaining / limit); wrong/no answer score zero. Server measures timing. Correctness/points are revealed only at question close.
- Question closes when everyone has answered, the timer expires, or the host manually closes it. Host advances from results to the next question.
- All players and their scores/rankings can be shown. Equal scores share one row; distinct scores get consecutive ranks (1, 2, 3).
- Manual pause freezes remaining time and blocks submissions; ordinary resume preserves submissions and remaining time.
- Editing the current question pauses it; committing the edit clears all submissions and restarts the question with its full timer when the host resumes. Earlier attempt is discarded without awarding points. Future questions can be edited. Completed questions are locked until the session finishes.
- Late joiners start at zero and may answer an open question with whatever time remains; timing is based on the question's original start, not their join time.
- A disconnected player's missing answer remains pending: disconnection does not shorten the timer. They may reconnect and answer before the deadline. If they do not, zero for that question; previous score remains.
- Reconnect tokens restore identity, scores and submissions without accounts. Host disconnect pauses the quiz; host reconnects and explicitly resumes.
- Host can remove players. Removal revokes their reconnect token and sends a best-effort REMOVED notice explaining that participation is no longer allowed. This notification plus server-side revocation is the protocol interpretation of Abdallah's request to send a token saying the player is not accepted; it does not issue a replacement credential. Earned points and already-accepted answers are preserved; they no longer count toward pending answers. Their name remains reserved for the session. A fresh join under a different name cannot reliably be prevented without accounts.
- Players may explicitly Leave room after a confirmation. This ends their participation/reconnect right for this session, preserves score/accepted answers and removes them from pending eligibility. Accidental disconnection is different: it retains eligibility and reconnect rights.
- Host may add, delete and reorder future questions during play. Current question position and completed questions remain locked; editing the current question still uses pause/restart. Cancelling an edit preserves original content, submissions and frozen time and stays paused until explicit resume.
- Reconnect tokens are saved locally for app-restart recovery while the server retains the room. If host never returns, retain the paused room until server shutdown. Playing again creates a fresh room with reset scores using the saved quiz.
- Approved product limits: question timer 1-300 seconds, 1-30 questions per quiz, player usernames up to 32 characters.
- Team update supersedes the earlier minimum-4/no-fixed-cap suggestion: 5-50 players per room, excluding the host. Proposed enforcement: at least five connected ACTIVE players to start; at most 50 ACTIVE identities, including disconnected players with reconnect rights. LEFT/REMOVED identities retain history but free an active slot. Falling below five after start does not pause or end the quiz.
- One host may manage only one unfinished room at a time. Multiple rooms require distinct hosts, one per room. Approved 2026-11-05: use a saved host identity on the device, without accounts. The same host can create successive rooms after finishing or explicitly closing the current room. Reconnection returns to the existing room; disconnection alone does not release it. Do not claim to recognize a person across devices.
- Multiple independent rooms and real LAN clients are required. Demonstration target: 3 simultaneous rooms with at least 5 players each.
- TCP carries commands, membership, question setup, answers, confirmations, results and recovery. UDP carries replaceable live countdown/progress snapshots. JSON and four-byte TCP length framing were accepted as the proposed basis for section 2.

## Open product choices

- Interface toolkit and terminal versus GUI; exact cheese icon artwork. Player wire format must not depend on a GUI toolkit.
- Mr Fat Rat theme is approved. Report presentation detail: each player's feeding progress reflects server-confirmed cumulative score after results; restore from that total on reconnect. Exact artwork, growth scale and animation remain UI implementation details and do not alter scoring or require new messages.
- Remaining numeric limits for frames, request rates and transport timeouts are engineering defaults, not course mandates.

## Proposed engineering defaults, requiring review rather than attributed approval

- Session state is retained in server memory; no server-crash recovery promise. Local reconnect-token persistence itself is approved.
- Never early-close an open question with zero eligible players. Host may close manually; otherwise the timer expires.
- Time uses integer milliseconds; scoring uses half-up rounding (floor(x + 0.5)), to make the agreed nearest-integer policy unambiguous.

See [section 2 protocol](section-2-protocol.md) for a proposed precise specification. Course source: [Project Requirements.pdf](../source_documents/Project%20Requirements.pdf).

## Team update 2026-11-04

The product is now named Fat-Rat. The team selected 5-50 players excluding the host, 2-8 choices per question, 1-30 questions per quiz, cheese selection symbols and one unfinished room per host. These replace earlier product limits. Functional, capacity, LAN and Wireshark validation belong in report section 4 Testing and Sample Runs; code remains unbuilt and capacity unverified.

## Completed report integration 2026-11-05

The protocol now specifies saved host credentials, creation recovery and CLOSE_ROOM. Finished/explicitly closed rooms release ownership; accidental disconnection does not. Early closure processes any already-due timeout, discards a still-unclosed attempt/edit without scoring it and preserves completed scores. The host profile credential persists across rooms and server restart. These are report design details, not implemented features. The report uses only black text/borders on white and editable native Word scoring equations.
