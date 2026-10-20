"""Reading the summary Markdown: sections to copy, and styled runs for the notes view."""

from __future__ import annotations

import re

FOLLOW_UP_HEADING = "Suggested follow-up"
ACTIONS_HEADING = "Action items"

_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`|(?<![\w*])_[^_]+_(?!\w)|(?<![\w*])\*[^*]+\*(?!\w))")


def title(md: str) -> str:
    for line in md.splitlines():
        if line.startswith("# "):
            return strip_inline(line[2:].strip())
    return ""


def section(md: str, heading: str) -> str:
    """The Markdown under `## heading`, up to the next heading of the same or higher level."""
    lines, inside = [], False
    for line in md.splitlines():
        match = re.match(r"(#{1,2})\s+(.*)", line)
        if match:
            if inside:
                break
            inside = match.group(1) == "##" and match.group(2).strip().lower() == heading.lower()
            continue
        if inside:
            lines.append(line)
    return "\n".join(lines).strip()


def strip_inline(text: str) -> str:
    """Remove bold, italic and code markers."""
    def unwrap(match):
        token = match.group(0)
        return token[2:-2] if token.startswith("**") else token[1:-1]
    return _INLINE.sub(unwrap, text)


def follow_up_message(md: str) -> str:
    """The ready-to-send message, without Markdown, for pasting into email or chat."""
    body = section(md, FOLLOW_UP_HEADING)
    if not body:
        return ""
    lines = body.splitlines()
    quoted = [line for line in lines if line.lstrip().startswith(">")]
    if quoted:
        # Keep blank quote lines as paragraph breaks.
        lines = [re.sub(r"^\s*>\s?", "", line) for line in quoted]
    text = "\n".join(strip_inline(line).rstrip() for line in lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_table(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue  # The |---|---| divider.
        rows.append(cells)
    return rows


def action_items(md: str) -> list[dict[str, str]]:
    """Rows of the action items table as {column: value}."""
    rows = parse_table(section(md, ACTIONS_HEADING).splitlines())
    if len(rows) < 2:
        return []
    header = [strip_inline(h) for h in rows[0]]
    items = []
    for row in rows[1:]:
        item = {h: strip_inline(row[i]) if i < len(row) else "" for i, h in enumerate(header)}
        if any(item.values()):
            items.append(item)
    return items


def action_items_text(md: str) -> str:
    """Action items as a plain list, e.g. "- Steve: Send the deck (due Friday)"."""
    items = action_items(md)
    if not items:
        return strip_inline(section(md, ACTIONS_HEADING))
    lines = []
    for item in items:
        owner = item.get("Owner", "")
        action = item.get("Action", "")
        line = f"- {owner}: {action}" if owner else f"- {action}"
        if item.get("Due"):
            line += f" (due {item['Due']})"
        if item.get("Notes"):
            line += f". {item['Notes']}" if not line.endswith(".") else f" {item['Notes']}"
        lines.append(line)
    return "\n".join(lines)


def inline_runs(text: str, base: tuple[str, ...]) -> list[tuple[str, tuple[str, ...]]]:
    runs = []
    pos = 0
    for match in _INLINE.finditer(text):
        if match.start() > pos:
            runs.append((text[pos:match.start()], base))
        token = match.group(0)
        if token.startswith("**"):
            runs.append((token[2:-2], base + ("bold",)))
        elif token.startswith("`"):
            runs.append((token[1:-1], base + ("code",)))
        else:
            runs.append((token[1:-1], base + ("italic",)))
        pos = match.end()
    if pos < len(text):
        runs.append((text[pos:], base))
    return runs


def render(md: str) -> list[tuple[str, tuple[str, ...]]]:
    """Turn Markdown into (text, tags) runs for a tk.Text.

    Tags: h1, h2, h3, p, bullet, bullet2, quote, item, meta, bold, italic, code.
    Tables become one entry per row: the first column in bold, then the second,
    with the other columns on a quieter line underneath, so they wrap neatly.
    """
    runs: list[tuple[str, tuple[str, ...]]] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped.startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            rows = parse_table(block)
            header, body = (rows[0], rows[1:]) if len(rows) > 1 else ([], rows)
            for row in body:
                runs += inline_runs(row[0] if row else "", ("item", "bold"))
                if len(row) > 1 and row[1]:
                    runs.append(("  ", ("item",)))
                    runs += inline_runs(row[1], ("item",))
                runs.append(("\n", ("item",)))
                extra = [(header[j] if j < len(header) else "", row[j])
                         for j in range(2, len(row)) if row[j]]
                if extra:
                    meta = "   ".join(f"{h}: {v}" if h else v for h, v in extra)
                    runs += inline_runs(meta, ("meta",))
                    runs.append(("\n", ("meta",)))
            continue
        heading = re.match(r"(#{1,6})\s+(.*)", stripped)
        if heading:
            tag = ("h1", "h2", "h3")[min(len(heading.group(1)), 3) - 1]
            runs.append((strip_inline(heading.group(2)), (tag,)))
            runs.append(("\n", (tag,)))
        elif re.match(r"[-*+]\s+", stripped):
            tag = "bullet2" if len(line) - len(line.lstrip()) >= 2 else "bullet"
            runs.append(("•  ", (tag,)))
            runs += inline_runs(re.sub(r"^[-*+]\s+", "", stripped), (tag,))
            runs.append(("\n", (tag,)))
        elif re.match(r"\d+[.)]\s+", stripped):
            number, rest = re.match(r"(\d+[.)])\s+(.*)", stripped).groups()
            runs.append((f"{number}  ", ("bullet",)))
            runs += inline_runs(rest, ("bullet",))
            runs.append(("\n", ("bullet",)))
        elif stripped.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            # One line per paragraph; the view adds the spacing between them.
            text = re.sub(r"\n\s*\n", "\n", "\n".join(quote).strip())
            runs += inline_runs(text, ("quote",))
            runs.append(("\n", ("quote",)))
            continue
        else:
            # Join wrapped lines of the same paragraph.
            para = [stripped]
            while (i + 1 < len(lines) and lines[i + 1].strip()
                   and not re.match(r"(#|[-*+]\s|\d+[.)]\s|>|\|)", lines[i + 1].strip())):
                i += 1
                para.append(lines[i].strip())
            runs += inline_runs(" ".join(para), ("p",))
            runs.append(("\n", ("p",)))
        i += 1
    return runs
