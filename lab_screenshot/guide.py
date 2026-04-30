#!/usr/bin/env python3
"""
guide.py — Parse, capture, and update lab guide markdown files.

Three-function API for agents to screenshot their way through a guide:

  1. parse_markers(text)           → find [SCREENSHOT: ...] markers
  2. capture_to_base64(page)       → screenshot the current Playwright page
  3. replace_markers(text, images) → swap markers for inline base64 images

Example agent workflow:

    from src.guide import parse_markers, capture_to_base64, replace_markers

    text = Path("guide.md").read_text()
    markers = parse_markers(text)

    # Agent drives the browser through the guide steps...
    images = {}
    for i, marker in enumerate(markers):
        # ... do the work described before this marker ...
        images[i] = capture_to_base64(page)

    updated = replace_markers(text, images)
    Path("guide.md").write_text(updated)
"""

import base64
import re
from dataclasses import dataclass
from typing import Optional


# Matches [SCREENSHOT: some description here]
# The description is everything between the colon and the closing bracket.
_MARKER_RE = re.compile(r"\[SCREENSHOT:\s*(.+?)\]")


@dataclass
class Marker:
    """A screenshot marker found in a markdown guide."""
    index: int           # 0-based position among all markers in the file
    line: int            # 1-based line number
    description: str     # text after "SCREENSHOT:"
    full_match: str      # the complete "[SCREENSHOT: ...]" string


def parse_markers(text: str) -> list[Marker]:
    """
    Find all [SCREENSHOT: description] markers in markdown text.

    Returns a list of Marker objects in document order.
    """
    markers = []
    for line_num, line in enumerate(text.splitlines(), start=1):
        for m in _MARKER_RE.finditer(line):
            markers.append(Marker(
                index=len(markers),
                line=line_num,
                description=m.group(1).strip(),
                full_match=m.group(0),
            ))
    return markers


def capture_to_base64(page, delay_ms: int = 500) -> str:
    """
    Take a PNG screenshot of the current Playwright page and return it
    as a markdown-ready base64 data URI.

    Args:
        page: A Playwright Page object (sync API).
        delay_ms: Brief pause before capture to let animations settle.
                  Set to 0 to skip.

    Returns:
        A string like "data:image/png;base64,iVBOR..." ready for use in
        markdown: ![desc](data:image/png;base64,...)
    """
    if delay_ms > 0:
        page.wait_for_timeout(delay_ms)

    png_bytes = page.screenshot(type="png")
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return f"data:image/png;base64,{b64}"


def replace_markers(text: str, images: dict[int, str]) -> str:
    """
    Replace [SCREENSHOT: description] markers with inline base64 images.

    Args:
        text: The original markdown text.
        images: Dict mapping marker index → base64 data URI string.
                Missing indices are left as-is (marker stays in the doc).

    Returns:
        Updated markdown with captured markers replaced by:
          ![description](data:image/png;base64,...)
    """
    markers = parse_markers(text)

    # Process in reverse order so replacements don't shift positions
    lines = text.splitlines(keepends=True)

    # Group markers by line (a line could theoretically have multiple)
    from collections import defaultdict
    by_line = defaultdict(list)
    for marker in markers:
        by_line[marker.line].append(marker)

    for line_num in sorted(by_line.keys(), reverse=True):
        line_idx = line_num - 1  # 0-based
        line = lines[line_idx]

        # Replace markers on this line in reverse column order
        for marker in sorted(by_line[line_num], key=lambda m: line.rfind(m.full_match), reverse=True):
            if marker.index not in images:
                continue

            data_uri = images[marker.index]
            alt_text = marker.description
            img_tag = f"![{alt_text}]({data_uri})"
            line = line.replace(marker.full_match, img_tag, 1)

        lines[line_idx] = line

    return "".join(lines)


def process_guide(
    input_path: str,
    output_path: Optional[str] = None,
    page=None,
    capture_fn=None,
) -> tuple[str, list[Marker]]:
    """
    Convenience: parse a guide file and return its markers.

    If page is provided, captures all markers immediately (useful for
    testing — in real use the agent captures one at a time).

    Args:
        input_path: Path to the markdown guide.
        output_path: If set, write the updated markdown here.
        page: Optional Playwright page — if given, captures every marker.
        capture_fn: Optional custom capture function(page) → base64 str.
                    Defaults to capture_to_base64.

    Returns:
        (updated_text, markers)
    """
    from pathlib import Path

    text = Path(input_path).read_text(encoding="utf-8")
    markers = parse_markers(text)

    images = {}
    if page is not None:
        fn = capture_fn or capture_to_base64
        for marker in markers:
            images[marker.index] = fn(page)

    updated = replace_markers(text, images) if images else text

    if output_path:
        Path(output_path).write_text(updated, encoding="utf-8")

    return updated, markers
