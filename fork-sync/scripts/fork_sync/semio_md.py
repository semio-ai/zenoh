"""Reading SEMIO.md: the check commands of its "Carrying ..." section and its
"Drop ... once ..." conditions. SEMIO.md is read from the human-reviewed
semio/<old> line, never from the branch Claude produced."""

from __future__ import annotations

import re
import shlex

HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
FENCE = re.compile(r"^\s*(```|~~~)\s*([\w+-]*)\s*$")
SHELL_LANGS = {"", "sh", "bash", "shell", "console", "zsh"}


def carrying_section(text: str) -> str | None:
    """The body of the first heading that starts with "Carrying"."""
    lines = text.splitlines()
    start = level = None
    in_fence = False
    for i, line in enumerate(lines):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = HEADING.match(line)
        if not m:
            continue
        if start is None:
            if m.group(2).lower().startswith("carrying"):
                start, level = i + 1, len(m.group(1))
        elif len(m.group(1)) <= level:
            return "\n".join(lines[start:i])
    return "\n".join(lines[start:]) if start is not None else None


def shell_blocks(section: str) -> list[str]:
    blocks, cur, lang = [], None, None
    for line in section.splitlines():
        m = FENCE.match(line)
        if m and cur is None:
            cur, lang = [], m.group(2).lower()
        elif m and cur is not None:
            if lang in SHELL_LANGS:
                blocks.append("\n".join(cur))
            cur = None
        elif cur is not None:
            cur.append(line)
    return blocks


def commands(block: str) -> list[str]:
    """Logical command lines of a shell block: continuations joined, comments
    and blank lines dropped."""
    out, buf = [], ""
    for raw in block.splitlines():
        line = raw.rstrip()
        if not buf and (not line.strip() or line.lstrip().startswith("#")):
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        out.append((buf + line).strip())
        buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def check_commands(text: str, programs: list[str]) -> list[str]:
    """Commands of the Carrying section whose program is in `programs`
    (cargo by default). git/gh lines of the procedure are left out."""
    section = carrying_section(text)
    if section is None:
        return []
    found = []
    for block in shell_blocks(section):
        for cmd in commands(block):
            try:
                argv = shlex.split(cmd)
            except ValueError:
                continue
            if argv and argv[0] in programs and not any(c in cmd for c in ("|", ";", "&&", "$(", "`", ">")):
                found.append(cmd)
    return found


DROP = re.compile(
    r"[^.!?\n]*\b(?:drop|remove|delete)\s+(?:it|them|this|these|the\s+\w+)\b[^.!?]*?\bonce\b[^.!?]*[.!?]",
    re.IGNORECASE,
)
SHA = re.compile(r"\b[0-9a-f]{7,40}\b")


def drop_conditions(text: str) -> list[dict]:
    """Each "Drop it once …" sentence with the bullet or paragraph it belongs to
    (its bold title when it has one) and the commit ids it mentions."""
    flat = re.sub(r"[ \t]*\n[ \t]*(?![-*] |\n)", " ", text)  # unwrap paragraphs, keep bullets
    out = []
    for para in re.split(r"\n(?=[-*] )|\n\n", flat):
        title_m = re.search(r"\*\*(.+?)\*\*", para)
        for m in DROP.finditer(para):
            sentence = re.sub(r"\s+", " ", m.group(0)).strip()
            shas = SHA.findall(sentence)
            # Links put the full id in the URL, the sentence may abbreviate it.
            for s in list(shas):
                full = re.search(rf"\b{s}[0-9a-f]*\b", para)
                if full:
                    shas[shas.index(s)] = full.group(0)
            out.append({
                "title": title_m.group(1) if title_m else para.strip().split("\n")[0][:80],
                "condition": sentence,
                "commits": shas,
            })
    return out
