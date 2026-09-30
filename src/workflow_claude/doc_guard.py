"""
Garde-fou de l'agent de documentation (Role.DOC_EDIT).

L'agent reçoit des outils d'édition : on ne peut pas se contenter de lui demander de ne toucher que la doc.
Après son passage, `enforce` compare l'état du dépôt à un instantané pris avant lui et ANNULE tout ce qui
n'est pas un fichier de documentation :
- fichier nouvellement modifié, hors liste blanche  -> restauré depuis HEAD (ou supprimé s'il est nouveau) ;
- fichier déjà modifié par le développeur (code validé) -> remis dans l'état de l'instantané s'il a bougé ;
- fichier du développeur « revenu » à HEAD par l'agent -> remis dans l'état de l'instantané ;
- suppression de fichier -> annulée.
Seuls les fichiers de documentation (README, CHANGELOG, *.md, *.rst, *.adoc, docs/) peuvent être créés ou modifiés.
"""

import logging
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Dict, List, Optional

from .clients.git_client import GitClient

logger = logging.getLogger("doc_guard")

DOC_SUFFIXES = {".md", ".mdx", ".markdown", ".rst", ".adoc"}
DOC_BASENAMES = {"changelog", "changes", "history", "readme"}


def is_doc_path(path: str) -> bool:
    """Vrai si `path` (relatif à la racine du dépôt) est un fichier de documentation modifiable."""
    posix = PurePosixPath(path.replace("\\", "/"))
    if ".git" in posix.parts:
        return False
    return posix.suffix.lower() in DOC_SUFFIXES or posix.name.lower() in DOC_BASENAMES


@dataclass
class DocSnapshot:
    """Contenu (octets) des fichiers déjà modifiés avant l'agent de documentation (le code validé)."""
    files: Dict[str, Optional[bytes]] = field(default_factory=dict)


@dataclass
class DocResult:
    kept: List[str] = field(default_factory=list)
    reverted: List[str] = field(default_factory=list)


def snapshot(git: GitClient) -> DocSnapshot:
    snap = DocSnapshot()
    for _code, path in git.status_entries():
        target = git.root / path
        snap.files[path] = target.read_bytes() if target.is_file() else None
    return snap


def enforce(git: GitClient, snap: DocSnapshot) -> DocResult:
    """Annule les modifications hors documentation faites depuis `snapshot` ; retourne ce qui est conservé/annulé."""
    result = DocResult()
    current = {path: code for code, path in git.status_entries()}

    for path, code in current.items():
        target = git.root / path
        before = snap.files.get(path, "__absent__")  # "__absent__" : fichier non modifié avant l'agent
        allowed = is_doc_path(path)
        deleted = not target.exists()

        if before == "__absent__":
            if allowed and not deleted:
                result.kept.append(path)
            else:
                _revert_new_change(git, path, code)
                result.reverted.append(path)
            continue

        # Fichier déjà modifié par le développeur : on ne tolère un changement que sur un fichier de doc
        now = target.read_bytes() if target.is_file() else None
        if now == before:
            continue
        if allowed and now is not None:
            result.kept.append(path)
        else:
            _restore_bytes(git, path, before)
            result.reverted.append(path)

    # Fichiers du développeur que l'agent a ramenés à HEAD (ils ne figurent plus dans `git status`)
    for path, before in snap.files.items():
        if path not in current and before is not None:
            _restore_bytes(git, path, before)
            result.reverted.append(path)

    for path in result.reverted:
        logger.warning(f"Modification hors documentation annulée : {path}")
    return result


def _revert_new_change(git: GitClient, path: str, code: str) -> None:
    target = git.root / path
    if code.strip() == "??":
        if target.is_file():
            target.unlink()
    else:
        git.restore_from_head(path)


def _restore_bytes(git: GitClient, path: str, content: Optional[bytes]) -> None:
    target = git.root / path
    if content is None:
        if target.is_file():
            target.unlink()
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
