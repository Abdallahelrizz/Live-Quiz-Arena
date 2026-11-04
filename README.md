# Fat-Rat

CSC430 Computer Networks course project at Lebanese American University.

**Status as of 5 November 2026: sections 1 and 2 describe the reviewed design. The quiz application has not been implemented or tested.** Document checks and Claude reviews do not establish that the application works. The instructor's request to demonstrate working behavior remains outstanding.

Fat-Rat is a planned multiplayer quiz using Python raw TCP/UDP sockets and threading. Each room has one noncompeting host, starts with at least five connected players, and supports a design limit of 50 ACTIVE players excluding the host. Quizzes contain 1–30 questions with 2–8 choices. Players select cheese symbols; Mr Fat Rat's feeding progress represents confirmed cumulative points. A saved host identity owns one unfinished room at a time and can create another after finishing or closing it.

- [Current status and remaining work](docs/status-and-next-steps.md)
- [Agreed gameplay](docs/gameplay-decisions.md)
- [Application-layer protocol](docs/section-2-protocol.md)
- [Review findings and dispositions](docs/protocol-review.md)
- [Sections 1 and 2 Word report](output/docx/CSC430_Fat_Rat_Sections_1_and_2.docx)

The report sections are due **6 November 2026**, as confirmed by Abdallah. The supplied course requirements are in [source_documents](source_documents/). No server/client run instructions exist yet; the only Python source currently present builds the report.
