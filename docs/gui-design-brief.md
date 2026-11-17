# Fat-Rat GUI: design brief

Written 2026-11-17 by Claude Code for Abdallah. It explains how to design the graphical interface and gives a prompt to paste into Claude. Everything in it matches what the server already does, so nothing designed from it will need new networking.

## How to do it

1. **Ask the instructor first** (one message): "May the interface be HTML/CSS shown inside our Python client's own window (pywebview), with all networking still raw Python sockets?" If yes, the design is used almost as-is. If no, the same design becomes the specification for a PySide6 (Python) interface, so the design work is not wasted either way.
2. **Open Claude and make a design.** Standalone Claude Design (claude.ai/design) closes by 14 December 2026 and now lives inside Claude: ask Claude for a *design* or *prototype*. Paste the prompt below as your first message.
3. **Work screen by screen.** Start with the three that matter most in the demo: the host's question screen (the projector), the player's answer pad, and the results/leaderboard. Then the rest. Ask for changes in plain words ("bigger countdown", "the cheeses look too similar").
4. **Check it against the list in "Screens".** Every state in that list needs a look, including the unhappy ones (reconnecting, removed, room full). Examiners like to see those.
5. **Hand it to Claude Code:** paste the design's claude.ai link in this project. Claude Code can read it, take the HTML/CSS, and connect every button and every number to `fatrat/client/core.py`. The build is a separate step and touches only a new `fatrat/client/gui/` folder.

Rules that keep the build easy:
- **It must work with no internet.** The demo runs on a local network that may have none. No web fonts, icon libraries or scripts loaded from the internet: use system fonts, inline SVG for icons and the rat, and plain CSS animations.
- **Design with fake data, no networking.** The page only shows things and has buttons. Python fills in the data.
- **One page per app** (host, player) with every screen as a state of that page is the easiest to connect.

## Prompt to paste into Claude

> Design the graphical interface for **Fat-Rat**, a multiplayer quiz game like Kahoot made for a university Computer Networks course. It must look extraordinary: playful, bold, cheese-and-rat themed, and very readable on a classroom projector. Make it a clickable HTML/CSS/JS prototype with fake data and no networking. It must work offline: no web fonts, CDNs or external scripts. Use system fonts, inline SVG and CSS animations only.
>
> **Two apps.**
> 1. **Host app**, which is also the shared screen projected for the room (design at 1920×1080, still usable at 1280×720). The host does not play.
> 2. **Player app**, one per player laptop (design at 1280×800, usable down to 1024×700).
>
> **Game rules the design must respect.**
> - A host creates a room and gets a 6-digit PIN. 5 to 50 players join with the PIN and a name (1 to 32 characters). The host can start only when at least 5 players are connected.
> - A quiz has 1 to 30 questions. Each question has 2 to 8 choices, exactly one correct, and a time limit of 1 to 300 seconds.
> - Each choice position has a cheese symbol: 1 Cheddar, 2 Brie, 3 Gouda, 4 Swiss, 5 Feta, 6 Mozzarella, 7 Parmesan, 8 Blue. The eight must be easy to tell apart by **shape and colour**, also for colour-blind players, and each also shows its number 1–8.
> - **Players never see the question or answer text.** The shared screen shows the question, the answer texts and their cheese symbols; the player's laptop shows only the cheese buttons. Players read the question on the projector.
> - A countdown is shown on both apps. Sometimes the time is only an estimate; show that subtly (for example "≈ 12 s"). When it reaches zero, show "waiting for results", not a result: the server decides.
> - The shared screen shows how many players have answered (for example "23 / 31 answered"), never who chose what before the question closes.
> - The first answer is final. After answering, the player sees "answer locked in" and waits.
> - Points: a correct answer gets 500 to 1000 points, more for answering faster; a wrong or missing answer gets 0. Correctness and points appear only when the question closes.
> - A question closes when everyone has answered, when time runs out, or when the host closes it. The host then moves to the next question.
> - Leaderboard: players with equal scores share a rank (1, 2, 2, 3). Show the top 10 after each question and everyone at the end. Players who left or were removed stay on the board with a small "left" or "removed" tag.
> - **Mr Fat Rat** is the mascot. Each player's rat gets fatter as their total score grows (fed cheese after each question). It is decoration only and never changes a score.
>
> **Host screens.**
> 1. Connect: server address, host name, Create room.
> 2. Quiz library: list of saved quizzes; create new, open, duplicate, delete. The host never has to write a file by hand.
> 3. Quiz editor: title; list of questions that can be added, deleted and reordered by dragging; per question: text, 2–8 choices, mark the correct one, time limit. Show validation errors in place (for example "needs at least 2 choices").
> 4. Lobby: a huge PIN and the server address for players to type; the list of joined players (room for 50 names) with offline players greyed; the count "7 / 50 players"; a Start button that stays disabled with the hint "5 connected players needed" until there are 5.
> 5. Question open (the main projector screen): "Question 3 of 10", the question text, each answer with its cheese symbol, a big countdown, the answered count. Controls: Pause, Close question, Edit this question, Players, End room.
> 6. Paused: the same screen dimmed with the reason: "Paused", "Editing this question", or "Host disconnected". Resume button.
> 7. Editing the current question: the editor for this one question over the paused game, with the warning "Saving restarts this question with its full time. Answers given so far are discarded." Save and Cancel.
> 8. Results: the correct answer highlighted (do not show how many chose each answer), the leaderboard top 10 with rats, and a Next button ("Show final ranking" after the last question). Why it closed: "Everyone answered", "Time is up" or "Closed by the host".
> 9. Final podium: the top 3 with the fattest rats, then the full ranking. A different ending for "The host ended the room early".
> 10. Players panel: each player with connected or offline, score, and Remove (with confirmation).
> 11. End room confirmation: "End the room for everyone? The open question will not be scored."
> 12. Upcoming questions can still be edited during the game; played ones show a lock.
>
> **Player screens.**
> 1. Join: server address, 6-digit PIN, name.
> 2. Waiting in the lobby: "You're in!", own name, own small rat.
> 3. Answer pad: 2 to 8 big cheese buttons (layouts for 2, 3, 4, 6 and 8), countdown, "Question 3 of 10". No text from the question.
> 4. Answer locked in: the chosen cheese, "waiting for the others".
> 5. Own result: correct or wrong, "+850 points", new total, rat being fed, current rank.
> 6. Paused: "The host paused the quiz" or "The host is editing this question" or "The host lost connection, please wait".
> 7. Final: own rank and score, fattest-rat celebration for the top 3.
>
> **Messages and errors, both apps.** Connecting; connection lost and reconnecting (attempt 2…); reconnected ("your place and score were kept"); removed by the host; "this session was opened on another device"; cannot rejoin. Join errors: no room with that PIN, name already taken, room full (50 players), quiz already finished, name must be 1–32 characters. Host errors: "The room changed just before your command, check and try again", "Too many requests, wait a moment". Errors appear next to what caused them, not as blocking pop-ups.
>
> Show me the main projector screen, the player answer pad and the results screen first.

## Screens and the data behind them (for the build)

Everything the GUI shows comes from the STATE message the server sends and from the countdown in `core.py`. Field names are from `fatrat/server/projection.py`.

| Screen | Data | Comes from |
|---|---|---|
| Lobby | PIN, players, connected or not | `identity["pin"]`; host STATE `participants` |
| Question (host) | number, text, choices with texts and symbols, time, answered count | STATE `question_index`, `question_count`, `current_question`; `countdown_ms()`; LIVE `answered_count`, `eligible_count` |
| Answer pad (player) | choices as symbols only, time, whether already answered | STATE `current_question.choices[].symbol_index`; `self.submitted_choice_id`; `countdown_ms()` |
| Approximate time | stale flag | `countdown_is_stale()` |
| Paused | reason | STATE `phase == "PAUSED"`, `pause_reason`: MANUAL, EDITING, HOST_DISCONNECTED |
| Results | correct choice, why it closed, own points and total, leaderboard | STATE `last_result` (`correct_choice_id`, `reason`, `own_result`), `leaderboard` |
| Final | ranking, how it ended | STATE `phase == "FINISHED"`, `finish_reason`: COMPLETED or HOST_CLOSED |
| Rat size | total score | `self.total_score` (player), `leaderboard` scores (host) |
| Connection | connecting, lost, reconnecting, resumed | `on_event("connection", ...)` |
| Notices | removed, replaced elsewhere, cannot rejoin | `on_event("notice", ...)` |
| Errors | code and message | `RequestFailed.code`; wording in `terminal_common.py: FRIENDLY` |

Not available, by design of the submitted protocol: who chose what, and how many chose each answer. The results screen shows the correct answer and the scores, not a vote chart. Adding it would change report section 2.
