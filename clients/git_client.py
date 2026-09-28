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

    def _run_git(self, args: List[str], check: bool = True) -> str:
        """Exécute une commande git dans le répertoire du projet."""
        cmd = ["git"] + args
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.project_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=check,
            )
            return proc.stdout.strip()
        except subprocess.CalledProcessError as e:
            err_msg = e.stderr.strip() or e.stdout.strip()
            raise GitClientError(f"Erreur Git ({' '.join(args)}) : {err_msg}")
        except FileNotFoundError:
            raise GitClientError("Binaire 'git' introuvable dans le PATH système.")

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
        Récupère l'intégralité du git diff (modifications indexées et non indexées)
        par rapport à HEAD.
        """
        try:
            # 1. Diff complet par rapport à HEAD si des commits existent
            diff = self._run_git(["diff", "HEAD"], check=False)
            if diff:
                return diff
            # 2. Si HEAD n'existe pas encore (dépôt vide), diff classique
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
            res = self._run_git(["restore", "."], check=False)
            if not res:
                # Fallback pour versions antérieures de git
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

