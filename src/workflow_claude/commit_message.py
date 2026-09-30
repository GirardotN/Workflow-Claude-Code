"""
Extraction d'un message de commit conventionnel depuis la sortie libre d'un agent.
"""

import re
from typing import Optional

CONVENTIONAL_TYPES = "feat|fix|refactor|chore|perf|style|test|docs|build|ci|revert"
_SUBJECT_RE = re.compile(rf"^(?:{CONVENTIONAL_TYPES})(?:\([\w./,-]+\))?!?: \S.*$")
_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*[ \t]*\r?\n(.*?)\r?\n?```", re.DOTALL)
_MAX_SUBJECT = 100


def _clean_line(line: str) -> str:
    return line.strip().strip("`*#> ").strip()


def _find_subject_index(lines: list) -> Optional[int]:
    for idx, line in enumerate(lines):
        if _SUBJECT_RE.match(_clean_line(line)):
            return idx
    return None


def extract_commit_message(doc_commit: str, fallback_subject: str) -> str:
    """
    Retourne un message de commit `type(scope): sujet` + corps.

    Stratégie : 1) bloc Markdown ``` contenant une ligne conventionnelle ; 2) première ligne
    conventionnelle du texte ; 3) repli `chore: <fallback_subject>`. Les balises ``` ne
    sont jamais conservées, et « feature request » n'est pas pris pour un type `feat`.
    """
    text = doc_commit or ""

    for block in _FENCE_RE.findall(text):
        lines = block.splitlines()
        idx = _find_subject_index(lines)
        if idx is not None:
            subject = _clean_line(lines[idx])[:_MAX_SUBJECT]
            body = "\n".join(lines[idx + 1:]).strip()
            return f"{subject}\n\n{body}" if body else subject

    lines = text.splitlines()
    idx = _find_subject_index(lines)
    if idx is not None:
        return _clean_line(lines[idx])[:_MAX_SUBJECT]

    summary = " ".join(fallback_subject.split())[:_MAX_SUBJECT - 7] or "workflow changes"
    return f"chore: {summary}"
