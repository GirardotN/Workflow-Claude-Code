"""
Préparation du texte envoyé au service tiers TypeSafe (Jev) : masquage des secrets et plafonnement
de la taille. Le diff peut contenir des clés, des jetons ou des mots de passe : on ne les transmet pas.

Le masquage est « au mieux » (expressions régulières sur les formats courants) : ce n'est pas une
garantie absolue. Pour ne rien envoyer du code, utilisez `--jev-send review-only`.
"""

import re
from typing import List, Optional, Tuple

REDACTED = "[REDACTED:{kind}]"

# (type, expression) : la valeur secrète est le groupe nommé « secret » s'il existe, sinon toute la correspondance
_PATTERNS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)),
    ("anthropic-key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
    ("openai-style-key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    ("github-token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+(?P<secret>[A-Za-z0-9._~+/\-]{16,}=*)")),
    ("url-credentials", re.compile(r"(?i)\b[a-z][a-z0-9+.\-]*://[^\s/:@]+:(?P<secret>[^\s/@]+)@")),
    (
        "credential-assignment",
        re.compile(
            r"(?i)\b[\w.\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key|auth)[\w.\-]*"
            r"\s*[:=]\s*['\"]?(?P<secret>[^\s'\",;)]{6,})"
        ),
    ),
]

_TRUNC_MARK = "\n[... {n} caractères omis ...]\n"


def mask_secrets(text: str, extra_secrets: Optional[List[str]] = None) -> str:
    """Remplace les secrets reconnus par `[REDACTED:<type>]`. `extra_secrets` : valeurs exactes à masquer."""
    if not text:
        return text
    for secret in extra_secrets or []:
        if secret and len(secret) >= 8:
            text = text.replace(secret, REDACTED.format(kind="configured-secret"))

    for kind, pattern in _PATTERNS:
        placeholder = REDACTED.format(kind=kind)

        def _replace(match, placeholder=placeholder):
            if "secret" in pattern.groupindex:
                start, end = match.span("secret")
                offset = match.start()
                whole = match.group(0)
                return whole[: start - offset] + placeholder + whole[end - offset:]
            return placeholder

        text = pattern.sub(_replace, text)
    return text


def truncate_middle(text: str, limit: int) -> str:
    """Garde le début et la fin d'un texte trop long, en signalant explicitement ce qui est omis."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    marker_budget = len(_TRUNC_MARK.format(n=len(text))) + 10
    keep = max(limit - marker_budget, 0)
    head = int(keep * 0.6)
    tail = keep - head
    omitted = len(text) - head - tail
    return text[:head] + _TRUNC_MARK.format(n=omitted) + (text[len(text) - tail:] if tail else "")


def build_validation_context(
    diff: str,
    review: str,
    review_label: str,
    mode: str = "full",
    max_chars: int = 60000,
    diff_label: str = "Git Diff",
    extra_secrets: Optional[List[str]] = None,
) -> str:
    """
    Construit l'« état » envoyé à Jev pour une validation.

    - `mode="full"` : diff (masqué, tronqué si besoin) + revue ; `mode="review-only"` : revue seule.
    - La revue est toujours conservée en priorité (plafonnée à la moitié du budget) ; le diff occupe le reste.
    """
    review_text = mask_secrets(review or "", extra_secrets)
    review_budget = max(max_chars // 2, 1000)
    review_text = truncate_middle(review_text, review_budget)
    review_block = f"{review_label} :\n{review_text}"

    if mode == "review-only":
        return review_block

    diff_budget = max(max_chars - len(review_block) - 50, 500)
    diff_text = truncate_middle(mask_secrets(diff or "", extra_secrets), diff_budget)
    return f"{diff_label} :\n{diff_text}\n\n{review_block}"


def prepare_context(text: str, max_chars: int = 60000, extra_secrets: Optional[List[str]] = None) -> str:
    """Masque et plafonne un texte libre (spécification envoyée à Jev pour le routage)."""
    return truncate_middle(mask_secrets(text or "", extra_secrets), max_chars)
