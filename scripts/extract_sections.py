"""Split the AsciiDoc spec into sections for the MCP resources.

Usage: python extract_sections.py <ble-live-protocol.adoc> <spec_sections.json>

Each `==`..`======` heading opens a section; the body is kept as AsciiDoc (readable as is,
tables included). Section ids are slugs of the heading, made unique with a numeric suffix.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^(={2,6})\s+(.+?)\s*$")


def slug(title: str) -> str:
    s = re.sub(r"pass:q\[.*?\]", "", title)
    s = re.sub(r"\[\[.*?\]\]", "", s)
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return s or "section"


def extract(adoc_path: Path) -> dict:
    lines = adoc_path.read_text(encoding="utf-8").splitlines()
    attrs = dict(re.findall(r"^:(\w+):\s*(.*)$", "\n".join(lines), re.M))
    sections: list[dict] = []
    used: dict[str, int] = {}
    current: dict | None = None
    preamble: list[str] = []
    for line in lines:
        m = HEADING.match(line)
        if m:
            level = len(m.group(1))
            title = re.sub(r"\s+", " ", m.group(2)).strip()
            base = slug(title)
            n = used.get(base, 0)
            used[base] = n + 1
            sid = base if n == 0 else f"{base}-{n + 1}"
            current = {"id": sid, "level": level, "title": title, "body": []}
            sections.append(current)
        elif current is not None:
            current["body"].append(line)
        else:
            preamble.append(line)
    for s in sections:
        s["body"] = "\n".join(s["body"]).strip()
    return {
        "spec_version": attrs.get("revnumber"),
        "revdate": attrs.get("revdate"),
        "source": adoc_path.name,
        "format": "asciidoc",
        "sections": sections,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    data = extract(Path(argv[1]))
    Path(argv[2]).write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(data['sections'])} sections -> {argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
