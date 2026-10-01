"""
Utilitaires de tests : création de dépôts Git jetables, déterministes sur tous les OS.
"""

import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def make_repo(files: Optional[dict] = None, commit: bool = True, identity: bool = True) -> "TempRepo":
    """Crée un dépôt temporaire (chemin résolu : /private/var sous macOS, noms longs sous Windows)."""
    return TempRepo(files or {}, commit=commit, identity=identity)


class TempRepo:
    DEFAULT_FILES = {
        "src/components/TabX.tsx": (
            "import React from 'react';\n\n"
            "export const TabX = ({ data }) => {\n"
            "  const sortedItems = [...data].sort((a, b) => a.name.localeCompare(b.name));\n"
            "  return <div>{sortedItems.map(i => <span key={i.id}>{i.name}</span>)}</div>;\n"
            "};\n"
        ),
        "src/components/TabY.tsx": "export const TabY = () => <div>Tab Y</div>;\n",
        "README.md": "# Test Project\n",
    }

    def __init__(self, files: dict, commit: bool = True, identity: bool = True):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name).resolve()
        git(self.path, "init", "-b", "main")
        git(self.path, "config", "core.autocrlf", "false")
        git(self.path, "config", "commit.gpgsign", "false")
        if identity:
            git(self.path, "config", "user.name", "TestUser")
            git(self.path, "config", "user.email", "test@example.com")
        else:
            git(self.path, "config", "user.useConfigOnly", "true")
        for rel, content in {**self.DEFAULT_FILES, **files}.items():
            self.write(rel, content)
        if commit:
            git(self.path, "add", "-A")
            # identité fournie à la volée : le commit initial fonctionne même sans identité configurée
            git(self.path, "-c", "user.name=Setup", "-c", "user.email=setup@example.com", "commit", "-m", "chore: initial commit")

    def write(self, rel: str, content: str) -> Path:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        return target

    def read(self, rel: str) -> str:
        with open(self.path / rel, encoding="utf-8", newline="") as f:
            return f.read()

    def git(self, *args: str, check: bool = True) -> str:
        return git(self.path, *args, check=check).stdout.strip()

    def branches(self) -> List[str]:
        return [b.strip().lstrip("* ") for b in self.git("branch", "--format=%(refname:short)").splitlines() if b.strip()]

    def current_branch(self) -> str:
        return self.git("rev-parse", "--abbrev-ref", "HEAD")

    def status(self) -> str:
        return self.git("status", "--porcelain")

    def stashes(self) -> List[str]:
        return [s for s in self.git("stash", "list").splitlines() if s.strip()]

    def cleanup(self) -> None:
        self._tmp.cleanup()
