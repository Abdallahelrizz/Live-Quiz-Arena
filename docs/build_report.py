"""Build the CSC430 sections 1 and 2 Word report and its sequence figures.

Uses bundled python-docx and Pillow. The working protocol remains the source
for the complete message catalogue; prose below presents its report version.
"""

from pathlib import Path
import re
import json
from PIL import Image, ImageDraw, ImageFont
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'output/docx'
QA = ROOT / 'tmp/report-qa'
OUT.mkdir(parents=True, exist_ok=True)
QA.mkdir(parents=True, exist_ok=True)
FONT = Path('/System/Library/Fonts/Supplemental')


def sequence(name, actors, events):
    """Draw a readable, deterministic sequence diagram with labelled transports."""
    width = 1900
    top = 170
    step = 104
    height = top + sum(156 if event[3] == 'note' else step for event in events) + 80
    im = Image.new('RGB', (width, height), 'white')
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(str(FONT / 'Arial.ttf'), 37)
    fb = ImageFont.truetype(str(FONT / 'Arial Bold.ttf'), 40)
    small = ImageFont.truetype(str(FONT / 'Arial.ttf'), 33)
    xs = [250, 950, 1650]
    for x, label in zip(xs, actors):
        d.rectangle((x - 205, 20, x + 205, 105), fill='white', outline='black', width=3)
        d.text((x, 61), label, font=fb, fill='black', anchor='mm')
        for y in range(108, height - 20, 25):
            d.line((x, y, x, min(y + 13, height - 20)), fill='black', width=2)
    pos = top
    for i, (a, b, text, kind) in enumerate(events):
        y = pos + 48
        pos += 156 if kind == 'note' else step
        if kind == 'note':
            d.rectangle((45, y - 39, width - 45, y + 39), fill='white', outline='black', width=2)
            d.text((width // 2, y), text, font=small, fill='black', anchor='mm')
            continue
        x1, x2 = xs[a], xs[b]
        if kind == 'udp':
            sign = 1 if x2 > x1 else -1
            for x in range(min(x1, x2), max(x1, x2), 26):
                d.line((x, y, min(x + 15, max(x1, x2)), y), fill='black', width=3)
        else:
            d.line((x1, y, x2, y), fill='black', width=3)
            sign = 1 if x2 > x1 else -1
        d.polygon([(x2, y), (x2 - sign * 22, y - 11), (x2 - sign * 22, y + 11)], fill='black')
        lines = text.split('\n')
        for j, line in enumerate(lines):
            d.text(((x1 + x2) // 2, y - 35 - 42 * (len(lines) - 1 - j)), line, font=f, fill='black', anchor='mm')
    path = QA / (name + '.png')
    im.save(path, dpi=(300, 300))
    return path, width, height


doc = Document()
sec = doc.sections[0]
sec.page_width = Inches(8.5)
sec.page_height = Inches(11)
sec.top_margin = Inches(.72)
sec.bottom_margin = Inches(.7)
sec.left_margin = sec.right_margin = Inches(.8)
sec.footer_distance = Inches(.3)
for sty in ('Normal', 'Body Text'):
    st = doc.styles[sty]
    st.font.name = 'Times New Roman'
    st.font.size = Pt(11.25)
    st.font.color.rgb = RGBColor(0, 0, 0)
    st.paragraph_format.space_after = Pt(6)
    st.paragraph_format.line_spacing = 1.05
for name, size in [('Title', 23), ('Subtitle', 12), ('Heading 1', 15), ('Heading 2', 12), ('Heading 3', 11.5)]:
    st = doc.styles[name]
    st.font.name = 'Times New Roman'
    st.font.size = Pt(size)
    st.font.color.rgb = RGBColor(0, 0, 0)
    st.font.bold = name != 'Subtitle'
    st.paragraph_format.space_before = Pt(12 if name.startswith('Heading') else 0)
    st.paragraph_format.space_after = Pt(6)
    st.paragraph_format.keep_with_next = True
    rpr = st.element.get_or_add_rPr()
    for child in list(rpr):
        if child.tag == qn('w:color'):
            for attr in ('themeColor', 'themeTint', 'themeShade'):
                child.attrib.pop(qn('w:' + attr), None)
    ppr = st.element.get_or_add_pPr()
    for elem in list(ppr):
        if elem.tag == qn('w:pBdr'):
            ppr.remove(elem)
doc.styles['Caption'].font.name = 'Times New Roman'
doc.styles['Caption'].font.size = Pt(10)
doc.styles['Caption'].font.color.rgb = RGBColor(0, 0, 0)
doc.styles['Caption'].paragraph_format.space_after = Pt(8)
doc.core_properties.title = 'Fat-Rat Technical Report Sections 1 and 2'
doc.core_properties.subject = 'CSC430 application description and application layer protocol'
doc.core_properties.author = 'CSC430 Project Team'
doc.core_properties.keywords = 'CSC430 TCP UDP quiz protocol'
footer = sec.footer.paragraphs[0]
footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
field = OxmlElement('w:fldSimple')
field.set(qn('w:instr'), 'PAGE')
footer._p.append(field)
footer.style = doc.styles['Normal']
for r in footer.runs:
    r.font.size = Pt(9)


def p(text='', bold=False):
    para = doc.add_paragraph()
    # Backticks distinguish field names in source, not literal report punctuation.
    chunks = re.split(r'(`[^`]+`)', text)
    for chunk in chunks:
        r = para.add_run(chunk[1:-1] if chunk.startswith('`') and chunk.endswith('`') else chunk)
        if chunk.startswith('`'):
            r.font.name = 'Courier New'
            r.font.size = Pt(9.5)
        r.bold = bold
    para.paragraph_format.widow_control = True
    return para


def h(text, level=2):
    return doc.add_paragraph(text, style=f'Heading {level}')


def math_run(text, plain=False):
    node = OxmlElement('m:r')
    props = OxmlElement('m:rPr')
    sty = OxmlElement('m:sty')
    sty.set(qn('m:val'), 'p' if plain else 'i')
    props.append(sty)
    node.append(props)
    wp = OxmlElement('w:rPr')
    fonts = OxmlElement('w:rFonts')
    for attr in ('ascii', 'hAnsi'):
        fonts.set(qn('w:' + attr), 'Cambria Math')
    wp.append(fonts)
    color = OxmlElement('w:color'); color.set(qn('w:val'), '000000'); wp.append(color)
    node.append(wp)
    t = OxmlElement('m:t'); t.text = text; node.append(t)
    return node


def fraction(numerator, denominator):
    node = OxmlElement('m:f')
    for tag, children in [('m:num', numerator), ('m:den', denominator)]:
        e = OxmlElement(tag)
        for child in children: e.append(child)
        node.append(e)
    return node


def floor_math(children):
    node = OxmlElement('m:d'); props = OxmlElement('m:dPr')
    for tag, value in [('m:begChr', '⌊'), ('m:endChr', '⌋'), ('m:grow', '1')]:
        e = OxmlElement(tag); e.set(qn('m:val'), value); props.append(e)
    node.append(props); e = OxmlElement('m:e')
    for child in children: e.append(child)
    node.append(e); return node


def scoring_equation(integer_form=False):
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para.paragraph_format.space_before = Pt(5)
    para.paragraph_format.space_after = Pt(8)
    block = OxmlElement('m:oMathPara')
    prop = OxmlElement('m:oMathParaPr'); jc = OxmlElement('m:jc')
    jc.set(qn('m:val'), 'center'); prop.append(jc); block.append(prop)
    eq = OxmlElement('m:oMath')
    eq.append(math_run('P', plain=False)); eq.append(math_run(' = ', plain=True))
    if integer_form:
        eq.append(math_run('500 + ', plain=True))
        eq.append(floor_math([fraction([math_run('1000', True), math_run('r'), math_run(' + ', True), math_run('L')], [math_run('2', True), math_run('L')])]))
    else:
        eq.append(floor_math([math_run('500 + 500 ', True), fraction([math_run('r')], [math_run('L')]), math_run(' + ', True), fraction([math_run('1', True)], [math_run('2', True)])]))
    block.append(eq); para._p.append(block)
    return para


def table(headers, rows, widths, size=10):
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False
    for col, w in zip(t.columns, widths):
        col.width = Inches(w)
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        e = OxmlElement('w:' + edge)
        for key, val in [('val', 'single'), ('sz', '4'), ('color', '000000')]:
            e.set(qn('w:' + key), val)
        borders.append(e)
    t._tbl.tblPr.append(borders)
    for i, row in enumerate([headers] + list(rows)):
        cells = t.rows[0].cells if i == 0 else t.add_row().cells
        trpr = cells[0]._tc.getparent().get_or_add_trPr()
        no_split = OxmlElement('w:cantSplit')
        trpr.append(no_split)
        if i == 0:
            repeat = OxmlElement('w:tblHeader')
            trpr.append(repeat)
        for j, (c, text) in enumerate(zip(cells, row)):
            c.width = Inches(widths[j])
            c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tcpr = c._tc.get_or_add_tcPr()
            margins = OxmlElement('w:tcMar')
            for edge, val in [('top', '70'), ('bottom', '70'), ('left', '85'), ('right', '85')]:
                e = OxmlElement('w:' + edge)
                e.set(qn('w:w'), val)
                e.set(qn('w:type'), 'dxa')
                margins.append(e)
            tcpr.append(margins)
            if i == 0:
                shade = OxmlElement('w:shd')
                shade.set(qn('w:fill'), 'FFFFFF')
                tcpr.append(shade)
            para = c.paragraphs[0]
            para.paragraph_format.space_after = Pt(1)
            para.paragraph_format.space_before = Pt(1)
            para.paragraph_format.line_spacing = 1.02
            para.paragraph_format.keep_with_next = i == 0
            if j == 1 and len(headers) == 3:
                para.alignment = WD_ALIGN_PARAGRAPH.CENTER
            run = para.add_run(str(text).replace('`', '').replace('→', 'to'))
            run.font.name = 'Times New Roman'
            run.font.size = Pt(size)
            run.bold = i == 0
    doc.add_paragraph().paragraph_format.space_after = Pt(1)
    return t


def caption(text):
    para = doc.add_paragraph(text, style='Caption')
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    return para


def figure(path, width, height, cap):
    # Keep both diagrams on a single legible page with explanatory captions.
    para = doc.add_paragraph()
    para.paragraph_format.space_after = Pt(3)
    para.paragraph_format.keep_with_next = True
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para.add_run().add_picture(str(path), width=Inches(6.7))
    caption(cap)


doc.add_paragraph('Fat-Rat Technical Report', style='Title')
doc.add_paragraph('Application Description and Application Layer Protocol', style='Subtitle')
p('Lebanese American University | CSC430 Computer Networks | Fall 2026')
p('5 November 2026')
p('This report specifies the planned behavior and communication protocol of Fat-Rat, a multiplayer quiz application using direct Python TCP and UDP sockets. It covers technical report sections 1 and 2. Implementation and experimental evidence will be documented in the later project sections.')

h('1 Application Description', 1)
h('1.1 Purpose and participants')
p('Fat-Rat is a real-time quiz application inspired by Kahoot. A noncompeting host creates a room, shares its PIN and manages timed multiple-choice questions. Players join with the PIN and a username to compete for server-awarded points. The host is a client and may run on a different device from the server.')
p('The shared host screen displays questions and answer choices paired with cheese symbols. Players receive only the corresponding symbols, countdown, acceptance feedback and their cumulative score. Eight symbol positions support up to eight choices. Omitting question and answer text makes player screenshots less useful for outside assistance, although it cannot prevent photographing the shared display.')
p('Each player\'s Mr Fat Rat mascot shows feeding progress based on the server-confirmed cumulative score. Progress changes after results and is restored on reconnect, without duplicate rewards or a separate scoring system. Artwork, feeding scale and animation are interface details.')
h('1.2 Preparing and joining a quiz')
p('The host creates and edits quizzes through the application, saves them as JSON files and loads them for later use. Each quiz contains between 1 and 30 questions. Each question has 2 to 8 answer choices, exactly one correct choice, and a host-selected time limit of 1 to 300 seconds. The host loads the quiz locally and uploads its validated content to the server before starting.')
p('The server issues a unique six-digit room PIN. Players join with a valid PIN and a unique username of up to 32 characters. Late joiners start at zero and use the current question\'s remaining time; joining during a pause or between questions does not start a personal timer. Section 2 defines exact validation.')
p('One server supports independent rooms, each with its own host, quiz, players, timer and scores. A room starts with at least five connected players and admits at most 50 ACTIVE players, excluding the host. Disconnected players retain a slot; permanent departures release it. Falling below five after start does not stop play. The required demonstration is three simultaneous rooms with five or more players each and a client on another LAN device [1]. Section 4 will establish tested capacity.')
p('A saved device identity allows each host one unfinished room. Reconnection restores it. Finishing or confirming Close Room permits another; closing early discards a still-unclosed question without points and keeps completed scores. Closing the app alone is a disconnection and does not release the room.')
h('1.3 Question flow and scoring')
p('The host starts the first question. The server opens its answer window and controls the deadline. A player may submit one valid answer per question attempt; once accepted, the answer is final. The client disables further submissions, while the server independently rejects invalid, duplicate or late requests. Acceptance feedback reveals neither correctness nor points while answering remains open. The shared screen shows the live number of answered players.')
p('A question closes when its time expires, every eligible player has answered, or the host closes it manually. Correctness, earned points and rankings are then revealed. The host explicitly advances to the next question. After the final question\'s results, advancing finishes the session and displays the complete final ranking.')
p('For a correct accepted answer, the score is rounded to the nearest integer, with half-points rounded upward:')
scoring_equation()
p('Here P is the awarded score, r is the remaining time when the server accepts the answer, and L is the question time limit. The floor brackets mean round downward; adding one half implements the agreed upward rounding of ties.')
p('The times r and L use the same unit. A wrong answer or no accepted answer earns zero. For a 20-second question, correct answers accepted after 5, 10 and 15 seconds earn 875, 750 and 625 points respectively. The server measures time; a player cannot supply their own timing or score. Paused time does not reduce the speed bonus.')
p('Players with equal cumulative scores share a single leaderboard row. Distinct scores receive consecutive ranks: for example, first place may list two names, followed by second and third place. The final leaderboard includes all participants and their scores, including retained records for players who left or were removed.')
h('1.4 Host controls and live editing')
p('A manual pause freezes the remaining time and stops answer acceptance while preserving existing submissions. Resuming continues with that saved duration. Editing the current question first pauses it and notifies players. Committing the edit clears all submissions for the discarded attempt, creates a new attempt and restores the full question time. The host explicitly resumes; changing the correct choice is safe because the discarded attempt earns no points. Cancelling the edit preserves the original question, answers and frozen time and leaves the quiz paused.')
p('The host may edit, add, delete or reorder future questions during play. The current question\'s position and all completed questions are protected. These operations change the future question list without altering the current timer or accepted answers. Completed content may be edited again after the session finishes, but historical results remain unchanged. Playing a saved quiz again creates a fresh room with reset scores.')
h('1.5 Connection failures and departures')
p('Player disconnection preserves identity, score, accepted answers and eligibility; it does not shorten the timer. A saved reconnect token restores the session without an account. Players can answer before the original deadline or receive zero. Detected host disconnection pauses an open question; the returning host explicitly resumes. Silent-failure detection is not instantaneous.')
p('Confirmed Leave room or host removal permanently revokes that player\'s token, reserves the name and removes pending-answer eligibility while retaining earned points and accepted answers. Removal remains effective if its notice is lost. Without accounts, a fresh identity under another name cannot be reliably prohibited. Rooms persist in server memory until shutdown; absent hosts leave unfinished rooms retained, paused if a question was open. Server crash recovery is outside scope.')

h('2 Application Layer Protocol', 1)
h('2.1 Architecture and transport choices')
p('The server is authoritative for membership, phase, timing, answer acceptance and scoring. Clients use one persistent TCP connection and one UDP socket. The implementation uses Python socket and threading facilities without HTTP, WebSocket or Socket.IO abstractions [1]. TCP carries commands and authoritative outcomes; UDP carries replaceable live display snapshots. TCP offers reliable ordered bytes, but application framing and acceptance replies are still required [2, 4]. UDP can lose, duplicate or reorder datagrams, so the application handles those properties explicitly [3].')
table(['Category', 'Transport', 'Justification'], [
    ('Creation, joining, reconnecting and leaving', 'TCP', 'Identity, membership and permanent departure need explicit outcomes.'),
    ('Quiz upload, edits and host controls', 'TCP', 'Ordered, complete commands prevent conflicting quiz state.'),
    ('Question setup and phase changes', 'TCP', 'Clients must use the authoritative question attempt and phase.'),
    ('Answers and acceptance replies', 'TCP', 'Lost or uncertain acceptance affects scoring and must be recoverable.'),
    ('Results, rankings, removal and state recovery', 'TCP', 'Authoritative results and access decisions must be delivered consistently.'),
    ('Liveness checks', 'TCP', 'UDP loss alone must not be treated as a disconnected host or player.'),
    ('Endpoint registration', 'TCP and UDP', 'TCP supplies a challenge; a UDP proof identifies the actual receiving endpoint.'),
    ('Countdown and answered counts', 'UDP', 'Each complete new snapshot replaces obsolete display information.')
], [2.3, .85, 3.75])
p('Example configurable ports are TCP 43000 and UDP 43001. Both sockets use IPv4. Every client, including one on the server machine, uses the same advertised server IPv4 address for TCP and UDP. The server binds UDP to that address so the source address is predictable. A loopback-only test uses 127.0.0.1 for both transports. Internet access and NAT traversal are not required. Traffic is plaintext on a trusted LAN; protection against interception and server-crash recovery are outside scope.')
h('2.2 Message encoding and boundaries')
p('All messages are UTF-8 JSON objects with v = 1, a type string and a data object. Unknown client fields, wrong types, duplicate keys, nonfinite numbers and invalid identifiers are rejected. JSON is encoded compactly without escaping ordinary Unicode text. IDs are UUIDv4 strings except the PIN, host_token, reconnect token and UDP nonce. Durations are integer milliseconds; revisions, sequence numbers and other numeric fields are nonnegative integers bounded by 2^63 - 1.')
p('A TCP message consists of a four-byte unsigned big-endian length N followed by exactly N JSON bytes. The length excludes the prefix and must be between 1 and 1,048,576 bytes. The receiver repeatedly reads until it has the complete header and body. This handles partial reads and multiple messages in the stream without losing boundaries. Sending uses an ordered writer and sendall. A partial frame has a 10-second deadline from its first byte. EOF, including EOF in the middle of a frame, ends the connection [4].')
p('A UDP datagram contains one complete JSON object with no length prefix and a maximum payload of 512 bytes. Oversized, truncated or malformed datagrams are dropped. The server sends about one LIVE_STATUS snapshot per second to each confirmed binding while the phase is OPEN or PAUSED. Snapshots contain no quiz text, scores, correctness or reconnect tokens. They provide meaningful live display updates; normal operation does not duplicate them as a per-second TCP stream.')
table(['Message kind', 'Additional envelope fields'], [
    ('Client TCP request', 'request_id; room_id after attachment. CREATE_ROOM and JOIN_ROOM are unattached; RESUME_SESSION puts room_id in data.'),
    ('RESULT or ERROR', 'reply_to matching request_id; room_id when known. ERROR reply_to may be null when no usable request ID exists.'),
    ('STATE', 'room_id and state_rev.'),
    ('PING and PONG', 'No request_id, reply_to or room_id; data.ping_id identifies the exchange.'),
    ('Other TCP session notices', 'room_id and the notice data listed in the catalogue.'),
    ('UDP_HELLO', 'session_id and binding_id; data.nonce proves the challenge.'),
    ('LIVE_STATUS', 'room_id, session_id, binding_id, question_id, attempt_id, control_rev and seq.')
], [1.65, 5.25])
p('actor_id is a stable host/player identity; a player\'s player_id is that same value. session_id identifies one TCP attachment and changes on reconnect. attempt_id identifies one presentation of a question and changes when current-question editing restarts it. state_rev orders reliable snapshots. control_rev changes for quiz controls and phase changes, but not a player leaving or ordinary connectivity changes. Leaving updates state_rev; an automatic close caused by leaving also advances control_rev. An actual host removal advances both revisions; joining and player connectivity advance state_rev only; voluntary leave also advances control_rev if it triggers automatic close. In-room host mutations include expected_control_rev, preventing a delayed command from executing in a newer control state. UDP must match the latest TCP-confirmed control revision; state_rev alone is not used to validate a tick.')
h('2.3 Shared data structures')
p('A Question contains question_id, text, choices, correct_choice_id and time_limit_ms. Question, choice and title text must be nonempty. choices is an ordered array of {choice_id, text}; question IDs are unique and choice IDs are unique within a question. The host generates these IDs, while the server validates them. choices contains 2 to 8 entries, correct_choice_id must identify one of them, and time_limit_ms must be an integer from 1,000 to 300,000; all text fields must be nonempty after trimming. Player question setup contains {question_id, attempt_id, choices:[{choice_id, symbol_index}], time_limit_ms}; symbol_index is the zero-based choice position from 0 to 7. The host uses the same ordering with the full text.')
p('A RankRow contains {rank, score, players:[{player_id, username, participation}]}. participation is ACTIVE, LEFT or REMOVED; disconnection does not change ACTIVE status. A QuestionResult contains {question_id, attempt_id, correct_choice_id, reason, own_result}. reason is ALL_ANSWERED, TIMEOUT or HOST_CLOSED. A player\'s own_result contains {choice_id or null, correct, points, total_score}; the host\'s own_result is null. Results refer to frozen closed attempts; STATE.leaderboard is the single ranking. On FINISHED, the full final ranking and its participation labels are frozen. Subsequent departure can revoke a token without rewriting that ranking.')
p('The reusable quiz list is separate from each immutable current/historical attempt. This prevents post-session edits from changing completed choices or scoring. Technical safety limits are 1,000 characters per question text, 300 per choice, 100 per title, and 512 KiB for compact quiz JSON. Every quiz mutation and join is checked against the worst-case full 1 MiB outgoing-state budget, including retained roster, longest participation labels, connection flags, optional recovery/pause fields, frozen question and result overhead. Character limits count Unicode code points. These are application limits, not limits imposed by TCP or UDP.')
h('2.4 Message catalogue')
p('H denotes the host client, P a player client, C either client and S the server. Every request receives RESULT or ERROR. The following rows define data fields in addition to the common envelope. Generic successful commands return an empty data object; identity and answer responses are defined explicitly. For a requester, RESULT precedes the resulting STATE. Only STATE changes a client\'s authoritative phase.')
source = (ROOT / 'docs/section-2-protocol.md').read_text()
a = source.index('| CREATE_ROOM |')
b = source.index('\nEvery TCP catalogue', a)
catalogue = []
for line in source[a:b].splitlines():
    if line.startswith('| '):
        cells = [x.strip() for x in line.split('|')[1:-1]]
        assert len(cells) == 4, cells
        catalogue.append(cells)
assert len(catalogue) == 32
groups = [
    ('Room membership and participation', {'CREATE_ROOM','JOIN_ROOM','RESUME_SESSION','CLOSE_ROOM','REMOVE_PLAYER','LEAVE_ROOM','GET_STATE'}),
    ('Quiz content and host controls', {'PUT_QUIZ','START_QUIZ','SUBMIT_ANSWER','PAUSE_QUIZ','RESUME_QUIZ','CLOSE_QUESTION','NEXT_QUESTION','EDIT_BEGIN','EDIT_COMMIT','EDIT_CANCEL','EDIT_QUESTION','ADD_QUESTION','DELETE_QUESTION','REORDER_QUESTIONS'}),
    ('Responses and live communication', {'REQUEST_UDP_BIND','UDP_HELLO','UDP_BOUND','LIVE_STATUS','RESULT','ERROR','STATE','PING','PONG','REMOVED','SESSION_REPLACED'})
]
for title, names in groups:
    h(title, 3)
    rows = []
    for name, direction, transport, desc in catalogue:
        if name in names:
            desc = desc.replace('approved replay policy: ', '')
            desc = desc.replace('under the approved', 'under the').replace('as specified in 2.1', 'as specified in section 2.10')
            display_name = name if name != 'REORDER_QUESTIONS' else 'REORDER_\nQUESTIONS'
            rows.append((display_name, direction.replace('→','to') + '\n' + transport, desc))
    table(['Message', 'Direction\nand transport', 'Data fields and behavior'], rows, [1.48, .78, 4.64], size=9.6)
h('2.5 State snapshots and visibility')
p('STATE data contains control_rev, phase, finish_reason, pause_reason, editing, host_connected, title, question_index, question_count, current_question, remaining_ms, self, leaderboard, last_result and live. question_index is zero-based or null before start/after HOST_CLOSED. In LOBBY, question_count is zero without a loaded quiz, otherwise its loaded count. phase is LOBBY, OPEN, PAUSED, RESULTS or FINISHED. finish_reason is null outside FINISHED and then COMPLETED or HOST_CLOSED, frozen at the first finish. pause_reason is null unless PAUSED, when it is MANUAL, EDITING or HOST_DISCONNECTED. editing is true exactly for an EDITING pause. EDIT_COMMIT and EDIT_CANCEL set pause_reason to MANUAL. host_connected is independent, so editing can remain active while the host is disconnected.')
p('self contains {actor_id, role, username, total_score, submitted_choice_id}; score and choice are null for the host. Host/player names must contain 1 to 32 Unicode code points after NFC normalization and trimming, with INVALID_USERNAME on failure; only player names reserve room membership. A player receives only the symbol-based current_question structure. In RESULTS and FINISHED/COMPLETED, current_question refers to the last closed question. After explicit room closure, both host and player current_question and question_index are null, remaining_ms is zero, submitted_choice_id is null, editing is false and pause_reason is null. question_count is frozen; last_result remains the last closed result, if any. The host additionally receives quiz (null before loading, otherwise {title, questions}) and participants:[{player_id, username, connected, participation, total_score}]. Host current_question also contains the question and choice text. The shared presentation hides correct-answer/editor metadata. Correct answer IDs never appear in player state before that attempt closes.')
p('leaderboard contains cumulative scores from completed questions, with zero-score rows initially. last_result is null or the most recently closed QuestionResult, privately projected for its recipient. live is always present: null in ordinary STATE or, in active recovery snapshots, {answered_count, eligible_count}. Each player receives their own serialized snapshot; one player\'s self or own_result must never be broadcast to others.')
p('A control_rev or host_connected change sends fresh STATE to the attached host and every attached ACTIVE player. Joining, removal and voluntary leave also update all remaining ACTIVE clients. A player-only disconnect/reconnect updates the host and reconnecting player; it need not notify other players of connectivity alone. GET_STATE replies only to its requester. STATE is not broadcast for every accepted answer: that acceptance is private and UDP carries live counts. Clients accept increasing state_rev values and equal-revision recovery refreshes; older snapshots are ignored.')
h('2.6 Timing and valid transitions')
p('The server measures now_ms = time.monotonic_ns() // 1,000,000. This clock is not affected by wall-clock adjustments [5]. An OPEN attempt has deadline_ms; remaining_ms = max(0, deadline_ms - now_ms). An answer is late when now_ms is at or after that deadline. The authoritative acceptance time is sampled after acquiring the room lock, rather than trusting a client timestamp or first-byte arrival. This policy has ordinary network and scheduling latency effects; it does not claim perfect latency fairness.')
p('Every serialized handler first closes an overdue OPEN attempt. The timer thread performs the same deadline check under the same lock, so late timer scheduling cannot admit a late answer. Pause stores remaining_ms, and resume sets deadline_ms to now_ms plus that saved duration. Accepted submissions record their remaining time; points are awarded exactly once when the attempt closes. With integer L = time_limit_ms and r = accepted remaining milliseconds, the same half-up score can be computed without floating-point rounding:')
scoring_equation(integer_form=True)
p('A new answer at r = 0 is rejected. Wrong answers and missing answers earn zero. A due timeout scores normally and makes an old-revision CLOSE_ROOM stale. The client reconciles fresh STATE before requesting closure again.')
table(['Phase and action', 'Result'], [
    ('LOBBY plus START_QUIZ', 'OPEN first question with a new attempt and full timer; a valid quiz and permitted player count are required.'),
    ('OPEN plus PAUSE_QUIZ', 'PAUSED with submissions and frozen time preserved.'),
    ('OPEN or PAUSED plus EDIT_BEGIN', 'PAUSED with editing active; preserve original question, submissions and frozen time.'),
    ('Editing plus EDIT_COMMIT', 'PAUSED with editing inactive, a new attempt, no submissions and full time. No score for the discarded attempt.'),
    ('Editing plus EDIT_CANCEL', 'PAUSED with original question, attempt, submissions and time unchanged.'),
    ('PAUSED plus RESUME_QUIZ', 'OPEN with saved time, or directly RESULTS if all remaining eligible players have already answered.'),
    ('OPEN plus deadline or all answered', 'RESULTS with immutable correctness, points and rankings.'),
    ('OPEN or nonediting PAUSED plus CLOSE_QUESTION', 'RESULTS; score existing accepted answers, unanswered zero.'),
    ('RESULTS plus NEXT_QUESTION', 'OPEN the next current future-list entry, or FINISHED if none remains.'),
    ('Any unfinished phase plus CLOSE_ROOM', 'FINISHED with HOST_CLOSED; discard unclosed question/edit, retain completed scores, freeze ranking and release host ownership.'),
    ('FINISHED plus CLOSE_ROOM', 'Successful no-op; preserve the original finish reason and final history.'),
    ('Host failure while OPEN', 'PAUSED when detected, unless the deadline already expired; explicit host resume required.')
], [2.3, 4.6])
p('Eligible players are all ACTIVE participants, including unanswered disconnected players and late joiners. A disconnection does not reduce this set. Explicit leave/removal does. Early close requires a nonempty eligible set in which everyone has answered; an empty room keeps its timer until expiry or manual close. Recheck all-answered after answers and eligibility changes, and on resume after departures during a pause. Live answered_count includes only accepted answers from eligible players, so it never exceeds eligible_count.')
h('2.7 Current and future question editing')
p('While OPEN, PAUSED or RESULTS, let k identify the current or just-closed question. Indices 0 through k are protected; only the suffix after k can be added to, deleted from or reordered. Current-question content changes use the EDIT_BEGIN and EDIT_COMMIT transaction. ADD_QUESTION inserts before a future ID or appends when its target is null. DELETE_QUESTION removes a future ID. REORDER_QUESTIONS supplies every current future ID exactly once, with no omissions or extra IDs. Question IDs must be unique within the candidate quiz; a deleted future question may be re-added with its saved ID.')
p('The server validates the complete candidate quiz and outgoing size budget before committing a change. Each actual content/list change advances the revisions and reliably refreshes every ACTIVE client\'s private STATE. It never changes the current attempt, timer or submissions unless it is EDIT_COMMIT. If a list change races NEXT_QUESTION, room-lock order and expected_control_rev allow one operation to succeed; the other synchronizes rather than accidentally modifying a newly current question. Deleting all future questions makes the current one final; appending before advancement makes the added question playable.')
p('In LOBBY and FINISHED, the reusable list is fully editable, with 1 to 30 questions. After FINISHED, question_index, question_count, current_question and recorded results remain the frozen history of that session even if reusable quiz content changes. A new room is required to replay it, and FINISHED releases the saved host identity for that new room. An identity reorder or reusable question/quiz replacement identical after canonical compact JSON serialization with recursively sorted object keys and preserved array order is a no-op: RESULT only, unchanged revisions and no cache entry. EDIT_COMMIT still restarts even when its text is identical. EDIT_CANCEL always stays paused; no editor action implicitly resumes play.')
h('2.8 UDP registration and recovery')
p('After attaching over TCP, the client sends REQUEST_UDP_BIND. The server returns a new binding_id, a random 128-bit UDP nonce encoded as 32 lowercase hexadecimal characters, and server_udp_port. It disables the old binding. The client sends UDP_HELLO from its receiving UDP socket to that port. The server checks the session, challenge, binding and TCP-peer source IP, records the observed source port, and confirms with UDP_BOUND over TCP. The client discards snapshots until that confirmation. Reconnection uses a new session_id and repeats this binding exchange; the reconnect token never travels over UDP.')
p('Challenges last 10 seconds. HELLO retries are limited to once per second, at most five sends before renewing over TCP with backoff capped at 30 seconds. A matching HELLO from the same endpoint can be confirmed again harmlessly; changing the source port needs a fresh binding. Unsolicited or invalid datagrams receive no response.')
p('The server reads an entire LIVE_STATUS snapshot atomically under the room lock, then sends outside it. Fields include the room, attachment, binding, question, attempt, control revision, sequence, remaining time and answered/eligible counts. In PAUSED, time is the stored frozen duration. The client checks server address/port, current binding, exact room/question/attempt/control revision, and a sequence greater than the last accepted one. Sequence starts at 1 for each binding; the client resets its last accepted value to 0 when that binding is confirmed. Packets for older or not-yet-confirmed future control states are discarded. A delayed pre-pause update cannot revive a timer after resuming because control_rev changed.')
p('An isolated lost packet is tolerated: the next complete snapshot supersedes it, and the client estimates the countdown locally between updates. Duplicate and reordered snapshots fail the sequence test. UDP never changes phase or determines acceptance. After three seconds without a matching snapshot in OPEN/PAUSED, mark progress stale and request GET_STATE no more than once per five seconds; persistent loss triggers rebinding with backoff. No stale polling occurs in LOBBY, RESULTS or FINISHED. Loss of UDP alone never disconnects a player or pauses the host. The display remains approximate under delay; the server deadline is authoritative.')
h('2.9 Errors retries and session recovery')
p('Malformed lengths, incomplete frames, invalid UTF-8/JSON, duplicate keys, nonfinite numbers, non-object payloads and unsupported versions are fatal to that connection. Send a bounded ERROR when safe, then close; never guess a new frame boundary. Well-formed requests with schema or state errors get a correlated ERROR and leave the connection usable. Oversized/malformed UDP is silently dropped. An invalid client must not stop the server or unrelated rooms.')
# Error table is sourced from the reviewed working specification to keep codes aligned.
a = source.index('| INVALID_PIN /')
b = source.index('\nERROR.retryable', a)
errors = []
for line in source[a:b].splitlines():
    if line.startswith('| '):
        cells = [x.strip() for x in line.split('|')[1:-1]]
        assert len(cells) == 2
        errors.append(cells)
table(['Error codes', 'Handling'], errors, [2.35, 4.55], size=9.8)
p('ERROR data contains code, message and retryable. retryable is true only for RATE_LIMITED or temporary SERVER_BUSY, optionally with retry_after_ms; a permanent byte-budget refusal is not retryable. STALE_STATE carries current_control_rev; a fresh private STATE follows either STALE_STATE or STALE_ATTEMPT. ERROR reason is required for SESSION_REVOKED; question_id, attempt_id and choice_id identify ALREADY_ANSWERED; retry_after_ms may accompany RATE_LIMITED or SERVER_BUSY. ALREADY_ANSWERED includes that player\'s accepted question, attempt and choice. A matching current closed attempt returns ANSWER_LATE; an older attempt after advancing returns STALE_ATTEMPT. Paused submissions return QUESTION_PAUSED. Invalid choice, unauthorized host actions and duplicate acceptance are rejected without changing scores.')
p('Each logical request has a fresh request_id. After an uncertain connection failure, reconcile fresh STATE before issuing another action, except for the persisted identical CREATE_ROOM recovery retry in section 2.10. The server retains each accepted answer\'s request fingerprint and outcome. An exact accepted-answer retry returns its historical acceptance and fresh STATE without re-awarding points or reviving a discarded attempt. A different submission follows normal duplicate rules. Answer and host-result fingerprints are keyed by (actor_id, request_id) and survive session replacement. For successful host mutations, retain the last 32 fingerprints and outcomes; a cached retry returns its outcome plus current STATE. An evicted original command cannot execute again because its expected_control_rev is stale. Reused cached IDs with different content are rejected. Errors and state/binding/join/resume operations are not cached; room creation has the separate ledger described below.')
p('One room lock serializes commands, expiry, replay checks, answer insertion, scoring, departures and quiz-list changes. Identity and connection generation are rechecked inside that lock before executing or replaying anything. Commit immutable events in order, then write outside the lock through one bounded writer queue per connection. A slow client is disconnected instead of blocking a room. A 10-request-per-second attachment limit excludes PONG; recovery does not depend on an unbounded result cache. These rules support the required threaded implementation; detailed implementation belongs in report section 3.')
p('The server sends PING five seconds after the previous valid PONG, allowing one outstanding ping. Missing PONG for 15 seconds, EOF, reset or write failure marks disconnection. A client treats 20 seconds without a complete TCP frame as stalled and attempts reconnect with 1, 2, 4, 8-second backoff capped at 15 seconds. A player token consists of 32 server-generated cryptographically random bytes encoded as unpadded URL-safe Base64 (43 characters), scoped to one actor and room. The host instead reuses its pre-saved device credential as reconnect_token, authorized only for its HOST actor in each retained room. The app stores it locally, separately from quiz content. Rooms and tokens remain in memory until server shutdown; completed rooms reject new joins but retained ACTIVE identities may resume to see final results.')
p('A SESSION_REPLACED recipient stops automatic resume until explicit user action, preventing two app instances repeatedly taking over. A valid resume replaces the old TCP attachment and UDP binding, preserving the actor\'s scores and submissions. Old queued requests and cleanup callbacks fail generation checks. Replacing an apparently still-connected host first applies the same pause rule as disconnection, then installs the new attachment; reconnecting never automatically resumes. Silent failure may consume time before detection, and an expired attempt is not retroactively paused. Losing an initial player JOIN_ROOM response before receiving its token cannot be securely recovered by username alone. An orphaned player remains ACTIVE and eligible until the host removes them; their reserved name is not released, so a tokenless rejoin needs another name. A host recovers a lost creation reply using its pre-saved credential and the creation ledger described below. A lost host credential leaves its room orphaned until shutdown.')
p('REMOVE_PLAYER commits REMOVED status and token revocation before attempting a final REMOVED notice. LEAVE_ROOM commits LEFT status and revocation before its final {left:true} result. Both preserve accepted answers for normal closing scores and remove eligibility. Notifications use the ordered writer with a bounded flush and then close; they never interrupt a partially written frame. Undelivered notification does not restore access. A retained revoked token receives SESSION_REVOKED with HOST_REMOVED or LEFT_ROOM reason on a fresh reconnect. The player app then discards its room credential. INVALID_TOKEN, including an unknown room after server restart, also stops automatic resume; a host clears the stale room attachment but retains its reusable profile credential. If resume succeeds after an uncertain leave, leave did not commit and the client may submit a fresh LEAVE_ROOM using the original confirmation. Confirmed departure is displayed only after {left:true} or a revoked-token denial. Removing an already LEFT/REMOVED identity is a no-op that preserves its original departure reason. After FINISHED, token revocation cannot change the frozen final leaderboard. No replacement denial token is issued. A plain connection drop preserves ACTIVE status and reconnect rights.')
h('2.10 Host identity and room ownership')
p('Before CREATE_ROOM, the host saves a random host_token and the pending request ID/username for the server address and TCP port. It is canonical unpadded URL-safe Base64 encoding of 32 random bytes, exactly 43 characters; malformed create/resume credentials return nonfatal INVALID_MESSAGE. Valid encoding must decode to 32 bytes and re-encode identically. A well-formed unmatched resume token returns INVALID_TOKEN. The server indexes SHA-256 of decoded bytes. It identifies a device profile, not a person across devices; names and IP addresses are not identities. Credentials stay out of quiz files, logs, shared displays and UDP.')
p('Under the registry lock, creation first checks its ledger of successful request IDs, fingerprints and room IDs, retained per host until shutdown. Exact reuse returns ROOM_ALREADY_CREATED with existing_room_id; changed content returns REQUEST_ID_REUSED. Otherwise an occupied unfinished-room slot returns HOST_HAS_ROOM with existing_room_id. After an uncertain creation reply, resend that exact saved request on a new connection, then resume existing_room_id using the saved credential. Ownership/ledger checks precede new-room allocation failures. The error envelope omits room_id because the socket is still unattached. This recovers a lost creation reply and prevents delayed duplicate creation after completion. Creating the next room requires a new request ID and an unattached TCP connection.')
p('Final NEXT_QUESTION or explicit CLOSE_ROOM releases ownership while retaining finished-room history. A lost closure reply is resolved by reconnecting and inspecting STATE. Disconnect alone never releases the slot; a returning host can close an abandoned lobby or paused room. After revision validation, a FINISHED close returns RESULT only, preserving the first finish_reason with no revision or cache change. A timeout advances the revision first; reconcile its fresh STATE before closing. Server restart clears rooms and ledgers; the saved host profile credential remains reusable.')
p('CREATE_ROOM checks and allocates atomically under the registry lock. NEXT_QUESTION and CLOSE_ROOM acquire registry then room lock for phase/ownership commit. Other handlers release registry lookup before locking a room; none acquires registry from inside a room lock. Release must match the exact old room ID, preventing an old close from freeing newer ownership. Socket I/O stays outside both locks.')
h('2.11 Example application messages')
p('The following answer request illustrates the JSON body. The four-byte TCP prefix is transmitted before it and is not printed here. IDs are valid example UUIDs, not live session credentials.')
example = {
    'v':1, 'type':'SUBMIT_ANSWER', 'request_id':'b3a4622d-a17c-43e6-8098-f00d59f3089b',
    'room_id':'3f9441d2-7ab1-47d2-b686-4c39745c46cb',
    'data':{'question_id':'3219d327-05b6-438c-a5dc-61387e3fb561','attempt_id':'cbdd3abd-22b5-47be-a9b1-ce2c60a8ed15','choice_id':'2de5e0c9-55b2-49b4-9429-4a61a3b9a2fb'}
}
for line in json.dumps(example, indent=2).splitlines():
    para = doc.add_paragraph()
    para.paragraph_format.space_after = Pt(0)
    para.paragraph_format.line_spacing = 1
    para.paragraph_format.keep_with_next = True
    run = para.add_run(line)
    run.font.name = 'Courier New'
    run.font.size = Pt(9)
p('The matching RESULT uses reply_to equal to request_id and data {accepted:true, question_id, attempt_id, choice_id}. It contains no correctness or points. A UDP LIVE_STATUS body instead uses remaining_ms, answered_count and eligible_count, accompanied by the binding, attempt, control revision and sequence envelope defined in section 2.2. A zero displayed countdown does not itself close the attempt; clients wait for authoritative STATE or an answer error.')
h('2.12 Typical exchanges and sequence diagrams')
p('Figure 1 shows room setup, player registration, UDP binding and one complete question. Solid arrows are TCP and dashed arrows are UDP. The host also binds its own UDP endpoint. Additional players use the same join sequence; at least five connected players must be present before START_QUIZ. Acceptance remains private until the server publishes results.').paragraph_format.keep_with_next = True
fig1 = sequence('session-sequence', ['Host client', 'Server', 'Player client'], [
    (0,1,'TCP CREATE_ROOM','tcp'), (1,0,'TCP RESULT and STATE\nPIN and host identity','tcp'),
    (0,1,'TCP PUT_QUIZ','tcp'), (2,1,'TCP JOIN_ROOM','tcp'),
    (1,2,'TCP RESULT and STATE\nPlayer identity and lobby','tcp'),
    (2,1,'TCP REQUEST_UDP_BIND','tcp'), (1,2,'TCP RESULT\nBinding and nonce','tcp'),
    (2,1,'UDP UDP_HELLO','udp'), (1,2,'TCP UDP_BOUND','tcp'),
    (0,1,'TCP START_QUIZ','tcp'),
    (1,2,'TCP STATE OPEN\nSymbols and initial time','tcp'),
    (1,2,'UDP LIVE_STATUS\nRepeated countdown and counts','udp'),
    (2,1,'TCP SUBMIT_ANSWER','tcp'), (1,2,'TCP RESULT accepted','tcp'),
    (0,0,'Server closes on deadline, all answered, or host request','note'),
    (1,2,'TCP STATE RESULTS\nCorrectness and rankings','tcp'),
    (0,1,'TCP NEXT_QUESTION','tcp')
])
figure(*fig1, 'Figure 1  Typical room setup and question exchange. Host state changes are also delivered over TCP; live host progress uses its own UDP binding.')
p('Figure 2 shows the current-question editing transaction and a player reconnecting after an uncertain answer response. A new attempt invalidates old submissions and UDP snapshots. Reconnection restores the server\'s accepted answer and establishes a fresh UDP binding.').paragraph_format.keep_with_next = True
fig2 = sequence('editing-sequence', ['Host client', 'Server', 'Player client'], [
    (2,1,'TCP SUBMIT_ANSWER\nAttempt A','tcp'), (1,2,'TCP RESULT accepted','tcp'),
    (0,1,'TCP EDIT_BEGIN','tcp'), (1,2,'TCP STATE PAUSED\nEditing in progress','tcp'),
    (0,1,'TCP EDIT_COMMIT','tcp'), (1,2,'TCP STATE PAUSED\nAttempt B and full time','tcp'),
    (0,0,'Attempt A submissions cleared; old ticks discarded','note'),
    (0,1,'TCP RESUME_QUIZ','tcp'), (1,2,'TCP STATE OPEN','tcp'),
    (2,1,'TCP SUBMIT_ANSWER\nAttempt B','tcp'),
    (0,0,'Connection drops before the acceptance reply arrives','note'),
    (2,1,'New TCP RESUME_SESSION','tcp'),
    (1,2,'TCP RESULT and STATE\nRestore committed submission','tcp'),
    (2,1,'TCP REQUEST_UDP_BIND','tcp'), (1,2,'TCP RESULT fresh binding','tcp'),
    (2,1,'UDP UDP_HELLO','udp'), (1,2,'TCP UDP_BOUND','tcp')
])
figure(*fig2, 'Figure 2  Current-question restart and reconnect recovery. The host explicitly resumes; the player reconciles STATE before another action.')
doc.add_page_break()
h('References')
for text in [
    '[1] Lebanese American University. CSC430 Computer Networks, Fall 2026. Course Project: Live Quiz Arena Socket Programming. Project Requirements.pdf, supplied course specification.',
    '[2] Eddy, W., editor. RFC 9293: Transmission Control Protocol. IETF, August 2022, section 2.2. https://www.rfc-editor.org/rfc/rfc9293.html',
    '[3] Eggert, L., Fairhurst, G., and Shepherd, G. RFC 8085: UDP Usage Guidelines. IETF, March 2017, sections 3.2 and 3.3. https://www.rfc-editor.org/rfc/rfc8085.html',
    '[4] Python Software Foundation. Socket Programming HOWTO. https://docs.python.org/3/howto/sockets.html',
    '[5] Python Software Foundation. time.monotonic and time.monotonic_ns. https://docs.python.org/3/library/time.html#time.monotonic',
    'AI assistance: OpenAI Codex and Anthropic Claude were used for protocol drafting, critical review and document preparation. The behavior described is a design specification; no implementation, load-test or Wireshark result is claimed in these sections.'
]:
    p(text)

path = OUT / 'CSC430_Fat_Rat_Sections_1_and_2.docx'
# Enforce black text, black borders and white backgrounds throughout OOXML.
for style in doc.styles:
    if hasattr(style, 'font'):
        style.font.color.rgb = RGBColor(0, 0, 0)
for part in doc.part.package.parts:
    element = getattr(part, 'element', None)
    if element is None: continue
    for node in element.iter():
        if node.tag == qn('w:color'):
            node.attrib.clear(); node.set(qn('w:val'), '000000')
        elif node.tag == qn('w:shd'):
            node.attrib.clear(); node.set(qn('w:fill'), 'FFFFFF'); node.set(qn('w:val'), 'clear')
# Neutralize generic legacy style parts as well as actively used document parts.
from lxml import etree
for part in doc.part.package.parts:
    if getattr(part, 'element', None) is None and str(part.partname).endswith('.xml'):
        try: legacy = etree.fromstring(part.blob)
        except etree.XMLSyntaxError: continue
        for node in legacy.iter():
            if node.tag == qn('w:color'):
                node.attrib.clear(); node.set(qn('w:val'), '000000')
            elif node.tag == qn('w:shd'):
                node.attrib.clear(); node.set(qn('w:fill'), 'FFFFFF'); node.set(qn('w:val'), 'clear')
        part._blob = etree.tostring(legacy, xml_declaration=True, encoding='UTF-8', standalone=True)
# Neutralize unused theme accents so later edits do not reintroduce colored headings.
from lxml import etree
for part in doc.part.package.parts:
    if part.partname.endswith('/theme1.xml'):
        theme = etree.fromstring(part.blob)
        ns = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main'}
        for node in theme.xpath('//a:clrScheme/*', namespaces=ns):
            label = etree.QName(node).localname
            for child in list(node): node.remove(child)
            color = etree.SubElement(node, '{'+ns['a']+'}srgbClr')
            color.set('val', 'FFFFFF' if label.startswith('lt') else '000000')
        part._blob = etree.tostring(theme, xml_declaration=True, encoding='UTF-8', standalone=True)
doc.save(path)
# Save reviewable report text independently of DOCX so Claude can inspect prose.
text = []
for element in doc.element.body:
    if element.tag == qn('w:p'):
        text.append(''.join(t.text or '' for t in element.findall('.//' + qn('w:t'))))
    elif element.tag == qn('w:tbl'):
        for row in element.findall(qn('w:tr')):
            text.append(' | '.join(''.join(t.text or '' for t in c.findall('.//' + qn('w:t'))) for c in row.findall(qn('w:tc'))))
(QA / 'report-review-text.txt').write_text('\n'.join(text))
print(path)
print('Catalogue:', len(catalogue), 'message types; figures:', 2)
