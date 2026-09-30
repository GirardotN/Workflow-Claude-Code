"""
Module de gestion des opérations Git pour l'orchestrateur in-repo.
Permet d'inspecter, capturer les diffs, annuler les régressions (rollback)
et commiter les changements validés directement dans le projet cible.

Toutes les commandes s'exécutent à la racine du dépôt (`git rev-parse --show-toplevel`),
même si le projet ciblé est un sous-dossier : le stash, le rollback et le diff
portent ainsi sur le même périmètre.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("git_client")

# Fichiers générés ou volumineux, sans intérêt pour une revue (exclus du diff relu par les agents)
DEFAULT_DIFF_EXCLUDES = [
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Pipfile.lock", "Cargo.lock",
    "go.sum", "composer.lock", "Gemfile.lock", "*.min.js", "*.min.css", "*.map",
    "dist/", "build/", "node_modules/", ".next/", "coverage/",
]


def diff_exclude_specs(patterns: List[str]) -> List[str]:
    """Convertit des motifs (`nom`, `*.ext`, `dossier/`) en pathspecs git « exclude » valables à toute profondeur."""
    specs = []
    for pattern in patterns:
        if pattern.endswith("/"):
            specs.append(f":(exclude,glob)**/{pattern}**")
        else:
            specs.append(f":(exclude,glob)**/{pattern}")
    return specs


class GitClientError(RuntimeError):
    """Exception levée en cas d'échec d'une commande Git."""
    pass


class GitClient:
    """
    Interface pour exécuter des opérations Git dans le répertoire du projet cible.
    """

    def __init__(self, project_dir: Optional[str] = None):
        self.project_dir = Path(project_dir).resolve() if project_dir else Path.cwd()
        self._root: Optional[Path] = None

    # ------------------------------------------------------------------
    # Exécution bas niveau
    # ------------------------------------------------------------------
    @staticmethod
    def _exec(args: List[str], cwd: Path, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
        """Exécute `git <args>` dans `cwd` avec une locale neutre et sans invite interactive."""
        env = {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}
        try:
            return subprocess.run(
                ["git"] + args,
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                input=input_text,
            )
        except FileNotFoundError:
            raise GitClientError("Binaire 'git' introuvable dans le PATH système.")

    @property
    def root(self) -> Path:
        """Racine du dépôt (répertoire de travail de toutes les commandes git)."""
        if self._root is None:
            proc = self._exec(["rev-parse", "--show-toplevel"], self.project_dir)
            top = proc.stdout.strip()
            self._root = Path(top).resolve() if proc.returncode == 0 and top else self.project_dir
        return self._root

    def _run_git_proc(self, args: List[str], input_text: Optional[str] = None) -> subprocess.CompletedProcess:
        """Exécute une commande git à la racine du dépôt et retourne le CompletedProcess complet."""
        return self._exec(args, self.root, input_text)

    def _run_git(self, args: List[str], check: bool = True, input_text: Optional[str] = None) -> str:
        """Exécute une commande git dans le dépôt du projet."""
        proc = self._run_git_proc(args, input_text)
        if check and proc.returncode != 0:
            err_msg = proc.stderr.strip() or proc.stdout.strip()
            raise GitClientError(f"Erreur Git ({' '.join(args)}) : {err_msg}")
        return proc.stdout.strip()

    # ------------------------------------------------------------------
    # Préconditions
    # ------------------------------------------------------------------
    def is_git_repository(self) -> bool:
        """Vérifie si le répertoire cible est un dépôt Git valide."""
        try:
            proc = self._exec(["rev-parse", "--is-inside-work-tree"], self.project_dir)
            return proc.returncode == 0 and proc.stdout.strip() == "true"
        except GitClientError:
            return False

    def has_commits(self) -> bool:
        """Vrai si HEAD pointe vers un commit (faux pour un dépôt vide tout juste initialisé)."""
        return self._run_git_proc(["rev-parse", "--verify", "--quiet", "HEAD"]).returncode == 0

    def has_identity(self) -> bool:
        """Vrai si git connaît user.name / user.email (sinon `git commit` échouera)."""
        return self._run_git_proc(["var", "GIT_COMMITTER_IDENT"]).returncode == 0

    def is_working_tree_clean(self) -> bool:
        """Vérifie si la copie de travail est propre (aucun fichier modifié non commité)."""
        try:
            status = self._run_git(["status", "--porcelain"])
            return len(status.strip()) == 0
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Lecture de l'état
    # ------------------------------------------------------------------
    def get_head_commit(self) -> str:
        """Récupère l'identifiant du commit HEAD actuel."""
        try:
            return self._run_git(["rev-parse", "--short", "HEAD"])
        except Exception:
            return "N/A"

    def get_head_ref(self) -> Tuple[Optional[str], str]:
        """
        Retourne (nom_de_branche, sha_complet). Le nom est None en HEAD détaché.
        """
        sha = self._run_git(["rev-parse", "HEAD"])
        proc = self._run_git_proc(["symbolic-ref", "--quiet", "--short", "HEAD"])
        branch = proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else None
        return branch, sha

    def get_current_branch(self) -> str:
        """Retourne le nom de la branche active courante ('HEAD' si détaché)."""
        try:
            branch, _ = self.get_head_ref()
            return branch or "HEAD"
        except Exception:
            return "HEAD"

    def get_diff(self) -> str:
        """
        Récupère l'intégralité du git diff (modifications indexées, non indexées
        et nouveaux fichiers créés) par rapport à HEAD.
        """
        try:
            # 1. Indexe les fichiers non suivis avec intent-to-add afin qu'ils apparaissent dans git diff
            self._run_git(["add", "-N", "."], check=False)

            # 2. Diff complet par rapport à HEAD si des commits existent
            diff = self._run_git(["diff", "HEAD"], check=False)
            if diff:
                return diff
            # 3. Si HEAD n'existe pas encore (dépôt vide), diff classique
            return self._run_git(["diff"], check=False)
        except Exception as e:
            logger.warning(f"Impossible de récupérer le git diff : {e}")
            return ""

    def get_diff_split(self, exclude: Optional[List[str]] = None) -> Tuple[str, List[str]]:
        """
        Diff par rapport à HEAD SANS les fichiers générés/volumineux (lockfiles, dist/, *.min.js...).
        Retourne (diff filtré, fichiers exclus) : les agents relisent le diff filtré, le rapport garde la liste.
        """
        patterns = DEFAULT_DIFF_EXCLUDES if exclude is None else exclude
        try:
            self._run_git(["add", "-N", "."], check=False)
            names_all = set(self._run_git(["diff", "HEAD", "--name-only"], check=False).splitlines())
            specs = diff_exclude_specs(patterns)
            diff = self._run_git(["diff", "HEAD", "--", "."] + specs, check=False)
            kept = set(self._run_git(["diff", "HEAD", "--name-only", "--", "."] + specs, check=False).splitlines())
            return diff, sorted(names_all - kept)
        except Exception as e:
            logger.warning(f"Impossible de récupérer le git diff : {e}")
            return "", []

    def get_modified_files(self) -> List[str]:
        """
        Retourne la liste des chemins de fichiers modifiés ou ajoutés.
        Utilise le format porcelain -z : robuste aux espaces, guillemets et renommages.
        """
        try:
            # --untracked-files=all : liste les fichiers d'un dossier non suivi (sinon git affiche « dossier/ »)
            proc = self._run_git_proc(["status", "--porcelain=v1", "-z", "--untracked-files=all"])
            if proc.returncode != 0:
                return []
            tokens = proc.stdout.split("\0")
            files: List[str] = []
            i = 0
            while i < len(tokens):
                entry = tokens[i]
                i += 1
                if len(entry) < 4:
                    continue
                status, path = entry[:2], entry[3:]
                if "R" in status or "C" in status:
                    i += 1  # le token suivant est l'ancien chemin
                files.append(path)
            return files
        except Exception:
            return []

    def untracked_files(self) -> List[str]:
        """Liste les fichiers non suivis et non ignorés (chemins relatifs à la racine du dépôt)."""
        proc = self._run_git_proc(["ls-files", "--others", "--exclude-standard", "-z"])
        if proc.returncode != 0:
            return []
        return [p for p in proc.stdout.split("\0") if p]

    def remove_untracked(self, paths: List[str]) -> None:
        """Supprime des fichiers non suivis précis (artefacts de tests, par exemple)."""
        if not paths:
            return
        for start in range(0, len(paths), 50):
            chunk = paths[start:start + 50]
            self._run_git(["clean", "-fd", "--"] + chunk, check=False)

    # ------------------------------------------------------------------
    # Annulation et commit
    # ------------------------------------------------------------------
    def rollback(self) -> None:
        """
        Annule toutes les modifications non commitées dans le dépôt : index, fichiers suivis
        (y compris ceux marqués intent-to-add) et fichiers non suivis non ignorés.
        Lève GitClientError si l'annulation échoue : l'état du dépôt n'est alors plus garanti.
        """
        logger.warning(f"Exécution du rollback Git dans {self.root}...")
        self._run_git(["reset", "--hard", "HEAD"])
        self._run_git(["clean", "-fd"])
        logger.info("Rollback Git complété : répertoire propre.")

    def commit(self, message: str) -> str:
        """
        Ajoute tous les fichiers modifiés et crée un commit Git (message passé sur stdin).
        Retourne le hash court du nouveau commit. Lève GitClientError en cas d'échec
        (identité absente, hook refusant le commit, rien à committer...).
        """
        self._run_git(["add", "-A"])
        self._run_git(["commit", "-F", "-"], input_text=message)
        new_head = self.get_head_commit()
        logger.info(f"Commit Git créé avec succès : [{new_head}] {message.splitlines()[0] if message else ''}")
        return new_head

    # ------------------------------------------------------------------
    # Branches
    # ------------------------------------------------------------------
    def branch_exists(self, branch_name: str) -> bool:
        proc = self._run_git_proc(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch_name}"])
        return proc.returncode == 0

    def unique_branch_name(self, base_name: str) -> str:
        """Retourne `base_name`, ou `base_name-2`, `-3`... si la branche existe déjà."""
        candidate = base_name
        suffix = 2
        while self.branch_exists(candidate):
            candidate = f"{base_name}-{suffix}"
            suffix += 1
        return candidate

    def create_and_checkout_branch(self, branch_name: str) -> None:
        """Crée une nouvelle branche et bascule immédiatement dessus."""
        logger.info(f"Création et bascule sur la branche d'isolation : {branch_name}")
        self._run_git(["checkout", "-b", branch_name])

    def checkout_branch(self, branch_name: str) -> None:
        """Bascule sur une branche existante."""
        logger.info(f"Bascule sur la branche : {branch_name}")
        self._run_git(["checkout", branch_name])

    def checkout_detached(self, sha: str) -> None:
        """Bascule en HEAD détaché sur un commit précis."""
        logger.info(f"Bascule en HEAD détaché sur : {sha[:10]}")
        self._run_git(["checkout", "--detach", sha])

    def count_commits_ahead(self, base_sha: str, branch_name: str) -> int:
        """Nombre de commits de `branch_name` absents de `base_sha`."""
        out = self._run_git(["rev-list", "--count", f"{base_sha}..{branch_name}"], check=False)
        return int(out) if out.isdigit() else 0

    def merge_branch(self, branch_to_merge: str, message: Optional[str] = None) -> bool:
        """
        Fusionne la branche spécifiée dans la branche courante.
        En cas de conflit ou d'échec, la fusion est abandonnée (`merge --abort`).
        """
        msg = message or f"merge: intégration des modifications validées de {branch_to_merge}"
        try:
            self._run_git(["merge", "--no-ff", "-m", msg, branch_to_merge])
            logger.info(f"Branche {branch_to_merge} fusionnée avec succès.")
            return True
        except Exception as e:
            logger.error(f"Échec de la fusion de la branche {branch_to_merge} : {e}")
            self._run_git(["merge", "--abort"], check=False)
            return False

    def delete_branch(self, branch_name: str, force: bool = False) -> bool:
        """Supprime une branche locale."""
        flag = "-D" if force else "-d"
        proc = self._run_git_proc(["branch", flag, branch_name])
        if proc.returncode == 0:
            logger.info(f"Branche {branch_name} supprimée ({flag}).")
            return True
        logger.warning(f"Impossible de supprimer la branche {branch_name} : {proc.stderr.strip()}")
        return False

    # ------------------------------------------------------------------
    # Stash
    # ------------------------------------------------------------------
    def _stash_entries(self) -> List[Tuple[str, str]]:
        """Liste les stashs sous forme (référence, sujet), du plus récent au plus ancien."""
        out = self._run_git(["stash", "list", "--format=%gd%x09%s"], check=False)
        entries = []
        for line in out.splitlines():
            ref, _, subject = line.partition("\t")
            if ref:
                entries.append((ref, subject))
        return entries

    def stash_push(self, message: str = "workflow-auto-stash") -> bool:
        """
        Met de côté les modifications non commitées de l'espace de travail (y compris untracked).
        Retourne True si un stash a été effectivement créé. La création est détectée
        en comparant la liste des stashs avant/après (indépendant de la langue de git).
        """
        try:
            if self.is_working_tree_clean():
                return False
            before = len(self._stash_entries())
            self._run_git(["stash", "push", "-u", "-m", message], check=False)
            entries = self._stash_entries()
            created = len(entries) > before and any(message in subject for _, subject in entries)
            if created:
                logger.info(f"Stash Guard : Modifications locales mises en réserve ({message}).")
            return created
        except Exception as e:
            logger.warning(f"Stash Guard : Impossible d'exécuter stash_push : {e}")
            return False

    def stash_pop(self, message: Optional[str] = None) -> bool:
        """
        Restaure les modifications préalablement mises en réserve par stash_push.
        Si `message` est fourni, le stash correspondant est retrouvé par son message
        (et non « le dernier »). En cas de conflit, le stash est conservé par git.
        """
        try:
            entries = self._stash_entries()
            if message is not None:
                ref = next((r for r, subject in entries if message in subject), None)
                if ref is None:
                    logger.warning(f"Stash Guard : stash '{message}' introuvable, rien à restaurer.")
                    return False
            else:
                if not entries:
                    return False
                ref = entries[0][0]

            proc = self._run_git_proc(["stash", "pop", ref])
            combined_output = (proc.stdout + " " + proc.stderr).strip()
            if proc.returncode != 0 or "CONFLICT" in combined_output:
                logger.warning(
                    f"Stash Guard : Conflit de fusion détecté lors de la restauration du stash ({combined_output[:200]}). "
                    "Vos modifications locales sont conservées dans le stash Git (git stash list)."
                )
                return False
            logger.info("Stash Guard : Modifications locales restaurées avec succès.")
            return True
        except Exception as e:
            logger.error(f"Stash Guard : Erreur lors de stash_pop : {e}")
            return False
