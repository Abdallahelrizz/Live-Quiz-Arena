"""Fat-Rat application-layer protocol (report section 2).

framing    - TCP: 4-byte big-endian length + UTF-8 JSON, strict JSON parsing
catalogue  - the 32 message types: sender, receiver, transport, data fields
messages   - validation of envelopes, data fields, RESULT/ERROR/STATE and quizzes
udp        - UDP datagrams (<= 512 bytes) and the LIVE_STATUS acceptance filter
"""
