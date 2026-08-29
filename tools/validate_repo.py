#!/usr/bin/env python3
"""QuestTeller repository validation.

Runs locally and in GitHub Actions.  It intentionally has no third-party
runtime dependencies so validation remains cheap and deterministic.
"""
from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "blogger/theme/questteller.xml"
MANIFEST = ROOT / "blogger/deploy.json"
CHRONICLES = ROOT / "content/chronicles/alisander"
PAGES = ROOT / "content/pages"

EXPECTED_CHAPTER_RE = re.compile(r"chapter-(\d{2})-[a-z0-9-]+\.html$")
BSKIN_RE = re.compile(
    r"<b:skin\b[^>]*><!\[CDATA\[(.*?)\]\]></b:skin>", re.DOTALL
)


class ContentAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.images: list[tuple[int, str | None]] = []
        self.inline_styles: list[int] = []
        self.h1_lines: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        amap = dict(attrs)
        line = self.getpos()[0]
        if tag.lower() == "img":
            self.images.append((line, amap.get("alt")))
        if "style" in amap:
            self.inline_styles.append(line)
        if tag.lower() == "h1":
            self.h1_lines.append(line)


def fail(errors: list[str], message: str) -> None:
    errors.append(message)


def validate_theme(errors: list[str]) -> None:
    if not THEME.exists():
        fail(errors, f"Missing theme: {THEME.relative_to(ROOT)}")
        return

    try:
        root = ET.parse(THEME).getroot()
    except ET.ParseError as exc:
        fail(errors, f"Theme XML is not well formed: {exc}")
        return

    if root.attrib.get("{http://www.google.com/2005/gml/b}responsive") != "true":
        # Blogger's prefixed attributes are retained by ElementTree using namespace form.
        if root.attrib.get("b:responsive") != "true":
            fail(errors, "Theme root must keep b:responsive='true'.")

    raw = THEME.read_text(encoding="utf-8")
    if "name='viewport'" not in raw and 'name="viewport"' not in raw:
        fail(errors, "Theme is missing the viewport meta tag.")
    if ":focus-visible" not in raw:
        fail(errors, "Theme is missing :focus-visible keyboard styles.")
    if "prefers-reduced-motion" not in raw:
        fail(errors, "Theme is missing prefers-reduced-motion handling.")

    match = BSKIN_RE.search(raw)
    if not match:
        fail(errors, "Could not locate the Blogger <b:skin> CDATA block.")
    else:
        css = match.group(1)
        # This is intentionally a coarse structural tripwire, not a full CSS parser.
        if css.count("{") != css.count("}"):
            fail(
                errors,
                f"Theme CSS braces are unbalanced: {css.count('{')} opening vs "
                f"{css.count('}')} closing.",
            )


def content_files() -> list[Path]:
    return sorted(CHRONICLES.glob("chapter-*.html")) + sorted(PAGES.glob("*.html"))


def validate_content(errors: list[str]) -> None:
    chapters = sorted(CHRONICLES.glob("chapter-*"))
    html_chapters = [p for p in chapters if p.is_file()]
    if not html_chapters:
        fail(errors, "No Chronicle chapter files found.")
        return

    numbers: list[int] = []
    for path in html_chapters:
        match = EXPECTED_CHAPTER_RE.fullmatch(path.name)
        if not match:
            fail(errors, f"Invalid chapter filename: {path.relative_to(ROOT)}")
            continue
        numbers.append(int(match.group(1)))

    if numbers and numbers != list(range(1, len(numbers) + 1)):
        fail(errors, f"Chapter numbering is not contiguous from 01: {numbers}")

    for path in content_files():
        text = path.read_text(encoding="utf-8")
        parser = ContentAudit()
        try:
            parser.feed(text)
        except Exception as exc:  # HTMLParser is lenient; any crash is worth failing.
            fail(errors, f"Could not parse {path.relative_to(ROOT)}: {exc}")
            continue

        for line, alt in parser.images:
            if alt is None or not alt.strip():
                fail(errors, f"Missing/empty image alt in {path.relative_to(ROOT)}:{line}")
        for line in parser.inline_styles:
            fail(errors, f"Inline style found in {path.relative_to(ROOT)}:{line}")

        # Blogger injects the post/page title; local body fragments should not create a second H1.
        for line in parser.h1_lines:
            fail(errors, f"Unexpected H1 in Blogger body fragment {path.relative_to(ROOT)}:{line}")


def validate_manifest(errors: list[str]) -> None:
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(errors, "Missing blogger/deploy.json")
        return
    except json.JSONDecodeError as exc:
        fail(errors, f"Invalid blogger/deploy.json: {exc}")
        return

    blog_url = data.get("blog_url", "")
    if not isinstance(blog_url, str) or not blog_url.startswith(("http://", "https://")):
        fail(errors, "blogger/deploy.json must contain a valid blog_url.")

    resources = data.get("resources")
    if not isinstance(resources, list) or not resources:
        fail(errors, "blogger/deploy.json must contain a non-empty resources list.")
        return

    seen_keys: set[tuple[str, str]] = set()
    seen_paths: set[str] = set()
    for item in resources:
        if not isinstance(item, dict):
            fail(errors, f"Manifest resource is not an object: {item!r}")
            continue
        kind = item.get("type")
        rel = item.get("path")
        title = item.get("title")
        if kind not in {"post", "page"}:
            fail(errors, f"Invalid manifest resource type for {rel!r}: {kind!r}")
        if not isinstance(rel, str) or not rel:
            fail(errors, "Manifest resource has an empty path.")
            continue
        if not isinstance(title, str) or not title.strip():
            fail(errors, f"Manifest resource has an empty title: {rel}")
        path = ROOT / rel
        if not path.is_file():
            fail(errors, f"Manifest path does not exist: {rel}")
        if rel in seen_paths:
            fail(errors, f"Duplicate manifest path: {rel}")
        seen_paths.add(rel)
        key = (str(kind), str(title))
        if key in seen_keys:
            fail(errors, f"Duplicate manifest remote key: {kind} / {title}")
        seen_keys.add(key)

    expected = {str(p.relative_to(ROOT)).replace("\\", "/") for p in content_files()}
    missing = expected - seen_paths
    extra = seen_paths - expected
    if missing:
        fail(errors, "Managed content missing from deploy manifest: " + ", ".join(sorted(missing)))
    if extra:
        fail(errors, "Deploy manifest contains non-managed paths: " + ", ".join(sorted(extra)))


def main() -> int:
    errors: list[str] = []
    validate_theme(errors)
    validate_content(errors)
    validate_manifest(errors)

    if errors:
        print("QuestTeller validation FAILED:\n", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("QuestTeller validation OK")
    print(f"  Theme: {THEME.relative_to(ROOT)}")
    print(f"  Chapters: {len(list(CHRONICLES.glob('chapter-*.html')))}")
    print(f"  Pages: {len(list(PAGES.glob('*.html')))}")
    print(f"  Deployment resources: {len(json.loads(MANIFEST.read_text())['resources'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
