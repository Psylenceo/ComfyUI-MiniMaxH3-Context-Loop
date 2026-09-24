#!/usr/bin/env python3
"""Check shipped Markdown's relative files and heading anchors, without network."""

import html
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r'\]\((?:<([^>]+)>|([^\s)]+))(?:\s+"[^"]*")?\)')


def heading_ids(text):
    ids, used = set(), set()
    fence = None
    for line in text.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        ids.update(re.findall(r'(?:id|name)=["\x27]([^"\x27]+)["\x27]', line))
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        label = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", match[1])
        label = re.sub(r"<[^>]*>", "", label)
        base = re.sub(r"[^\w\s-]", "", html.unescape(label).lower()).replace(" ", "-")
        slug, index = base, 0
        while slug in used:
            index += 1
            slug = f"{base}-{index}"
        used.add(slug)
        ids.add(slug)
    return ids


def broken_links(path):
    failures = []
    for match in LINK.finditer(path.read_text(encoding="utf-8")):
        raw = match[1] or match[2]
        if re.match(r"[a-zA-Z][\w+.-]*:", raw) or raw.startswith("//"):
            continue
        location, _, anchor = raw.partition("#")
        target = (path.parent / unquote(location)).resolve() if location else path
        if not target.exists():
            failures.append(f"{path}: missing {raw}")
        elif anchor and target.suffix.lower() == ".md":
            if unquote(anchor) not in heading_ids(target.read_text(encoding="utf-8")):
                failures.append(f"{path}: missing heading {raw}")
    return failures


def main():
    assert heading_ids("# One\n# One\n# One-1\n## A / **B**\n```\n# Ignored\n```") == {
        "one", "one-1", "one-1-1", "a--b"}
    # Exercise actual link checks as well as the heading parser.
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "a guide.md").write_text("# Present\n", encoding="utf-8")
        source = root / "index.md"
        source.write_text(
            "[ok](<a guide.md#present>) [encoded](a%20guide.md#present) "
            "[external](https://example.com/not-fetched) "
            "[bad](absent.md) [bad anchor](<a guide.md#absent>)", encoding="utf-8")
        assert len(broken_links(source)) == 2

    paths = set(ROOT.glob("*.md"))
    for folder in ("docs", "example_workflows", "tools/v06", "selflift_runtime"):
        paths.update((ROOT / folder).rglob("*.md"))
    failures = [failure for path in sorted(paths) for failure in broken_links(path)]
    assert not failures, "\n".join(failures)
    print(f"Documentation links: {len(paths)} Markdown files, local paths and anchors OK")


if __name__ == "__main__":
    main()
