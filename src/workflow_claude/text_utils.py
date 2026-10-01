"""
Utilitaires de texte purs (sans dépendance au reste du package).
"""

import re
from typing import Optional, Tuple


def normalize_test_output(output: str) -> str:
    """
    Normalise la sortie des tests en éliminant les variations de temps d'exécution
    et de millisecondes (ex: 'in 0.42s', '45ms') pour une comparaison déterministe.
    """
    cleaned = re.sub(r"\b\d+(\.\d+)?\s*(s|ms|seconds?)\b", "", output, flags=re.IGNORECASE)
    cleaned = re.sub(r"\(duration:\s*\d+(\.\d+)?s\)", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"(\[|\()\s*\d+%\s*(\]|\))", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def clean_code_output(raw_code: str) -> Tuple[str, Optional[str]]:
    """
    Extrait le code source contenu dans un bloc Markdown (```<lang> ... ```)
    et détecte le tag de langage si présent.
    """
    if not raw_code:
        return "", None

    match = re.search(r"```([a-zA-Z0-9_#+-]*)\s*\n([\s\S]*?)\n```", raw_code)
    if match:
        lang = match.group(1).strip().lower() or None
        code = match.group(2)
        return code, lang

    return raw_code.strip(), None
