"""
Module de gestion des opérations Git pour l'orchestrateur in-repo.
Permet d'inspecter, capturer les diffs, annuler les régressions (rollback)
et commiter les changements validés directement dans le projet cible.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("git_client")


class GitClientError(RuntimeError):
    """Exception levée en cas d'échec d'une commande Git."""
    pass


class GitClient:
    """
    Interface pour exécuter des opérations Git dans le répertoire du projet cible.
    """

    def __init__(self, project_dir: Optional[str] = None):
        self.project_dir = Path(project_dir).resolve() if project_dir else Path.cwd()

    def _run_git_proc(self, args: List[str]) -> subprocess.CompletedProcess:
        """Exécute une commande git et retourne le CompletedProcess complet."""
        cmd = ["git"] + args
        env = {**os.environ, "LC_ALL": "C"}
        try:
            return subprocess.run(
                cmd,
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except FileNotFoundError:
            raise GitClientError("Binaire 'git' introuvable dans le PATH système.")

    def _run_git(self, args: List[str], check: bool = True) -> str:
        """Exécute une commande git dans le répertoire du projet."""
        proc = self._run_git_proc(args)
        if check and proc.returncode != 0:
            err_msg = proc.stderr.strip() or proc.stdout.strip()
            raise GitClientError(f"Erreur Git ({' '.join(args)}) : {err_msg}")
        return proc.stdout.strip()

    def is_git_repository(self) -> bool:
        """Vérifie si le répertoire cible est un dépôt Git valide."""
        try:
            out = self._run_git(["rev-parse", "--is-inside-work-tree"], check=False)
            return out == "true"
        except Exception:
            return False

    def is_working_tree_clean(self) -> bool:
        """Vérifie si la copie de travail est propre (aucun fichier modifié non commité)."""
        try:
            status = self._run_git(["status", "--porcelain"])
            return len(status.strip()) == 0
        except Exception:
            return False

    def get_head_commit(self) -> str:
        """Récupère l'identifiant du commit HEAD actuel."""
        try:
            return self._run_git(["rev-parse", "--short", "HEAD"])
        except Exception:
            return "N/A"

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

    def get_modified_files(self) -> List[str]:
        """Retourne la liste des chemins de fichiers modifiés ou ajoutés."""
        try:
            out = self._run_git(["status", "--porcelain"])
            files = []
            for line in out.splitlines():
                line = line.strip()
                if line:
                    # Format porcelain : " M path/to/file" ou "?? path/to/file"
                    parts = line.split(maxsplit=1)
                    if len(parts) == 2:
                        files.append(parts[1])
            return files
        except Exception:
            return []

    def rollback(self) -> None:
        """
        Annule toutes les modifications non commitées dans le projet.
        Restaure les fichiers modifiés et supprime les fichiers non suivis créés.
        """
        logger.warning(f"Exécution du rollback Git dans {self.project_dir}...")
        try:
            # 1. Restauration des fichiers modifiés
            # Essai avec 'git restore .' (Git moderne >= 2.23)
            try:
                self._run_git(["restore", "."])
            except GitClientError:
                # Fallback pour versions antérieures de git (< 2.23)
                self._run_git(["checkout", "--", "."], check=False)

            # 2. Nettoyage des fichiers non suivis
            self._run_git(["clean", "-fd"], check=False)
            logger.info("Rollback Git complété : répertoire propre.")
        except Exception as e:
            logger.error(f"Erreur lors du rollback Git : {e}")


    def commit(self, message: str) -> Optional[str]:
        """
        Ajoute tous les fichiers modifiés et crée un commit Git.
        Retourne le hash du nouveau commit créé.
        """
        try:
            self._run_git(["add", "-A"])
            self._run_git(["commit", "-m", message])
            new_head = self.get_head_commit()
            logger.info(f"Commit Git créé avec succès : [{new_head}] {message.splitlines()[0]}")
            return new_head
        except Exception as e:
            logger.error(f"Échec de la création du commit Git : {e}")
            return None

    def get_current_branch(self) -> str:
        """Retourne le nom de la branche active courante."""
        try:
            branch = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
            return branch.strip()
        except Exception:
            return "HEAD"

    def create_and_checkout_branch(self, branch_name: str) -> None:
        """Crée une nouvelle branche et bascule immédiatement dessus."""
        logger.info(f"Création et bascule sur la branche d'isolation : {branch_name}")
        self._run_git(["checkout", "-b", branch_name])

    def checkout_branch(self, branch_name: str) -> None:
        """Bascule sur une branche existante."""
        logger.info(f"Bascule sur la branche : {branch_name}")
        self._run_git(["checkout", branch_name])

    def merge_branch(self, branch_to_merge: str, message: Optional[str] = None) -> bool:
        """
        Fusionne la branche spécifiée dans la branche courante.
        """
        msg = message or f"merge: intégration des modifications validées de {branch_to_merge}"
        try:
            self._run_git(["merge", "--no-ff", "-m", msg, branch_to_merge])
            logger.info(f"Branche {branch_to_merge} fusionnée avec succès.")
            return True
        except Exception as e:
            logger.error(f"Échec de la fusion de la branche {branch_to_merge} : {e}")
            return False

    def delete_branch(self, branch_name: str, force: bool = False) -> bool:
        """Supprime une branche locale."""
        flag = "-D" if force else "-d"
        try:
            self._run_git(["branch", flag, branch_name], check=False)
            logger.info(f"Branche {branch_name} supprimée ({flag}).")
            return True
        except Exception as e:
            logger.warning(f"Impossible de supprimer la branche {branch_name} : {e}")
            return False

    def stash_push(self, message: str = "workflow-auto-stash") -> bool:
        """
        Met de côté les modifications non commitées de l'espace de travail (y compris untracked).
        Retourne True si un stash a été effectivement créé.
        """
        try:
            if self.is_working_tree_clean():
                return False
            out = self._run_git(["stash", "push", "-u", "-m", message], check=False)
            if "Saved working directory" in out or "saved" in out.lower() or "sauvegard" in out.lower():
                logger.info(f"Stash Guard : Modifications locales mises en réserve ({message}).")
                return True
            stash_list = self._run_git(["stash", "list"], check=False)
            if message in stash_list:
                logger.info(f"Stash Guard : Modifications locales mises en réserve ({message}).")
                return True
            return False
        except Exception as e:
            logger.warning(f"Stash Guard : Impossible d'exécuter stash_push : {e}")
            return False

    def stash_pop(self) -> bool:
        """
        Restaure les modifications préalablement mises en réserve par stash_push.
        Alerte et gère proprement les éventuels conflits de fusion.
        """
        try:
            proc = self._run_git_proc(["stash", "pop"])
            combined_output = (proc.stdout + " " + proc.stderr).strip()
            if proc.returncode != 0 or "CONFLICT" in combined_output:
                logger.warning(
                    f"Stash Guard : Conflit de fusion détecté lors de la restauration du stash ({combined_output[:200]}). "
                    "Vos modifications locales sont conservées dans le stash Git."
                )
                return False
            logger.info("Stash Guard : Modifications locales restaurées avec succès.")
            return True
        except Exception as e:
            logger.error(f"Stash Guard : Erreur lors de stash_pop : {e}")
            return False


