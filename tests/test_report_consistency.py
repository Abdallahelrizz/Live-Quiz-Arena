"""Check that the code's message catalogue matches the submitted report.

Reads the message and error-code tables straight out of the Word report
(output/docx/...docx) and the working specification (docs/section-2-protocol.md)
and compares them with fatrat/protocol/catalogue.py. If someone edits the
report or the code without the other, this test fails.
"""

from __future__ import annotations

import re
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from fatrat.protocol.catalogue import CATALOGUE, ERROR_CODES

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "output" / "docx" / "CSC430_Fat_Rat_Sections_1_and_2.docx"
SPEC = ROOT / "docs" / "section-2-protocol.md"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def report_tables():
    root = ET.fromstring(zipfile.ZipFile(REPORT).read("word/document.xml"))
    for table in root.iter(W + "tbl"):
        yield [["".join(t.text or "" for t in cell.iter(W + "t")) for cell in row.iter(W + "tc")]
               for row in table.iter(W + "tr")]


def code_catalogue():
    return {name: (spec.sender, spec.receiver, spec.transport) for name, spec in CATALOGUE.items()}


@unittest.skipUnless(REPORT.exists(), "Word report not found")
class WordReportTest(unittest.TestCase):
    def test_message_tables_match_catalogue(self):
        found = {}
        for rows in report_tables():
            if rows[0][0] == "Message" and rows[0][1].startswith("Direction"):
                for cells in rows[1:]:
                    match = re.fullmatch(r"([HPCS]) to ([HPCS])(TCP|UDP)", cells[1])
                    self.assertIsNotNone(match, f"unexpected direction cell {cells[1]!r}")
                    found[cells[0]] = match.groups()
        self.assertEqual(len(found), 32)
        self.assertEqual(found, code_catalogue())

    def test_error_table_matches_catalogue(self):
        codes = set()
        for rows in report_tables():
            if rows[0][0] == "Error codes":
                for cells in rows[1:]:
                    codes.update(code.strip() for code in cells[0].split("/"))
        self.assertEqual(codes, set(ERROR_CODES))


@unittest.skipUnless(SPEC.exists(), "protocol specification not found")
class MarkdownSpecTest(unittest.TestCase):
    def test_catalogue_table_matches(self):
        found = {}
        for line in SPEC.read_text(encoding="utf-8").splitlines():
            match = re.match(r"\| ([A-Z_]+) \| ([HPCS]) → ([HPCS]) \| (TCP|UDP) \|", line)
            if match:
                found[match.group(1)] = match.groups()[1:]
        self.assertEqual(found, code_catalogue())


if __name__ == "__main__":
    unittest.main()
