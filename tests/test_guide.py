#!/usr/bin/env python3
"""Tests for guide.py — marker parsing and replacement."""

import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from lab_screenshot.guide import parse_markers, replace_markers, Marker


SAMPLE_GUIDE = """\
# Lab: Configure Auth Server

1. Navigate to **Security > API > Authorization Servers**
2. Click **Add Authorization Server**

[SCREENSHOT: Auth server list page]

3. Fill in the form:
   - Name: `MCP Adapter Auth Server`
   - Audience: `api://mcp`

[SCREENSHOT: Auth server creation form]

4. Click **Save**

## Add Scopes

1. Click the **Scopes** tab
2. Add these scopes: `sfdc:read`, `sfdc:write`, `snow:read`, `snow:write`, `mcp:read`

[SCREENSHOT: Scopes tab with all five scopes]

Done!
"""


def test_parse_markers_count():
    markers = parse_markers(SAMPLE_GUIDE)
    assert len(markers) == 3, f"Expected 3 markers, got {len(markers)}"


def test_parse_markers_descriptions():
    markers = parse_markers(SAMPLE_GUIDE)
    assert markers[0].description == "Auth server list page"
    assert markers[1].description == "Auth server creation form"
    assert markers[2].description == "Scopes tab with all five scopes"


def test_parse_markers_indices():
    markers = parse_markers(SAMPLE_GUIDE)
    assert markers[0].index == 0
    assert markers[1].index == 1
    assert markers[2].index == 2


def test_parse_markers_lines():
    markers = parse_markers(SAMPLE_GUIDE)
    lines = SAMPLE_GUIDE.splitlines()
    for m in markers:
        assert m.full_match in lines[m.line - 1]


def test_replace_all_markers():
    fake_b64 = "data:image/png;base64,AAAA"
    images = {0: fake_b64, 1: fake_b64, 2: fake_b64}
    result = replace_markers(SAMPLE_GUIDE, images)

    assert "[SCREENSHOT:" not in result
    assert "![Auth server list page](data:image/png;base64,AAAA)" in result
    assert "![Auth server creation form](data:image/png;base64,AAAA)" in result
    assert "![Scopes tab with all five scopes](data:image/png;base64,AAAA)" in result


def test_replace_partial_markers():
    """Only replace markers that have images; leave others as-is."""
    fake_b64 = "data:image/png;base64,BBBB"
    images = {1: fake_b64}  # Only the second marker
    result = replace_markers(SAMPLE_GUIDE, images)

    assert "[SCREENSHOT: Auth server list page]" in result  # untouched
    assert "![Auth server creation form](data:image/png;base64,BBBB)" in result
    assert "[SCREENSHOT: Scopes tab with all five scopes]" in result  # untouched


def test_replace_no_markers():
    """No images → text unchanged."""
    result = replace_markers(SAMPLE_GUIDE, {})
    assert result == SAMPLE_GUIDE


def test_empty_document():
    markers = parse_markers("")
    assert markers == []


def test_no_markers_in_text():
    text = "# Just a heading\n\nSome text.\n"
    markers = parse_markers(text)
    assert markers == []


def test_surrounding_text_preserved():
    """Text around markers is not altered."""
    fake_b64 = "data:image/png;base64,CCCC"
    images = {0: fake_b64, 1: fake_b64, 2: fake_b64}
    result = replace_markers(SAMPLE_GUIDE, images)

    assert "# Lab: Configure Auth Server" in result
    assert "Navigate to **Security > API > Authorization Servers**" in result
    assert "Done!" in result


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS  {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {test.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
