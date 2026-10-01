"""
Isolation transactionnelle d'un run In-Repo : Stash Guard + branche de travail.

`IsolatedRun` est un gestionnaire de contexte qui garantit, sur TOUTE sortie
(succès, échec métier, exception, Ctrl-C) :
  1. l'annulation des modifications non validées (rollback),
  2. le retour sur la branche (ou le commit détaché) d'origine,
  3. la suppression de la branche de travail si elle est vide,
  4. la restauration du stash de l'utilisateur, EN DERNIER, sur la branche d'origine.
"""

import logging
import os
import time
from typing import Optional

from .clients.git_client import GitClient, GitClientError

logger = logging.getLogger("isolation")


class WorkflowPreconditionError(RuntimeError):
    """Le dépôt n'est pas dans un état permettant de lancer le workflow en sécurité."""
    pass


class IsolatedRun:
    """Stash Guard + branche d'isolation, avec nettoyage garanti."""

    def __init__(
        self,
        git: GitClient,
        use_branch: bool = True,
        will_commit: bool = True,
        branch_prefix: str = "workflow/ai-",
    ):
        self.git = git
        self.use_branch = use_branch
        self.will_commit = will_commit
        self.branch_prefix = branch_prefix

        self.original_branch: Optional[str] = None  # None en HEAD détaché
        self.original_sha: str = ""
        self.work_branch: Optional[str] = None
        self.stash_message: Optional[str] = None
        self.stashed = False
        self.stash_restored: Optional[bool] = None
        self.closed = False       # True une fois la sortie « propre » effectuée par leave()
        self.merged = False
        self.branch_kept = False

    # ------------------------------------------------------------------
    # Entrée
    # ------------------------------------------------------------------
    def __enter__(self) -> "IsolatedRun":
        self._check_preconditions()

        if not self.git.is_working_tree_clean():
            self.stash_message = f"workflow-auto-stash-{int(time.time())}-{os.getpid()}"
            logger.info("Stash Guard : modifications locales non commitées détectées, mise en réserve via git stash...")
            self.stashed = self.git.stash_push(self.stash_message)
            if not self.git.is_working_tree_clean():
                # Rien n'a été modifié côté utilisateur : on s'arrête avant toute action.
                if self.stashed:
                    self.stash_restored = self.git.stash_pop(self.stash_message)
                raise WorkflowPreconditionError(
                    "Impossible de mettre en réserve vos modifications locales (git stash a échoué ou laissé des "
                    "changements, par exemple dans un sous-module). Commitez-les ou nettoyez l'arbre de travail."
                )

        try:
            self.original_branch, self.original_sha = self.git.get_head_ref()
            if self.use_branch:
                name = self.git.unique_branch_name(f"{self.branch_prefix}{int(time.time())}")
                self.git.create_and_checkout_branch(name)
                self.work_branch = name
                origin = self.original_branch or f"HEAD détaché @{self.original_sha[:10]}"
                logger.info(f"Isolation Git active : branche '{name}' créée depuis '{origin}'.")
        except Exception:
            # Échec après le stash : on restaure immédiatement le travail de l'utilisateur.
            self._restore_stash()
            raise
        return self

    def _check_preconditions(self) -> None:
        if not self.git.has_commits():
            raise WorkflowPreconditionError(
                "Le dépôt ne contient aucun commit : créez un premier commit avant de lancer le workflow."
            )
        if (self.use_branch or self.will_commit) and not self.git.has_identity():
            raise WorkflowPreconditionError(
                "Identité Git absente : configurez-la avant de lancer le workflow "
                "(git config user.name \"Votre Nom\" && git config user.email vous@exemple.com)."
            )

    # ------------------------------------------------------------------
    # Étapes « propres »
    # ------------------------------------------------------------------
    def has_changes(self) -> bool:
        return not self.git.is_working_tree_clean()

    def commit(self, message: str) -> str:
        """Commit les modifications validées sur la branche de travail."""
        return self.git.commit(message)

    def leave(self, merge: bool = False) -> None:
        """
        Sortie propre après un succès : retour sur la branche d'origine, fusion éventuelle,
        suppression de la branche de travail si elle est vide ou fusionnée.
        Le stash est restauré ensuite, dans __exit__.
        """
        if self.work_branch:
            ahead = self.git.count_commits_ahead(self.original_sha, self.work_branch)
            self._checkout_original()

            if ahead == 0:
                self.git.delete_branch(self.work_branch, force=True)
                self.work_branch = None
            elif merge and self.original_branch:
                self.merged = self.git.merge_branch(self.work_branch)
                if self.merged:
                    self.git.delete_branch(self.work_branch)
                else:
                    logger.warning(f"Fusion impossible : la branche '{self.work_branch}' est conservée pour inspection.")
                    self.branch_kept = True
            else:
                self.branch_kept = True
        self.closed = True

    # ------------------------------------------------------------------
    # Sortie
    # ------------------------------------------------------------------
    def __exit__(self, exc_type, exc, tb) -> bool:
        try:
            if not self.closed:
                self._abort()
        finally:
            self._restore_stash()
        return False  # ne jamais masquer une exception

    def _checkout_original(self) -> None:
        if self.original_branch:
            self.git.checkout_branch(self.original_branch)
        elif self.original_sha:
            self.git.checkout_detached(self.original_sha)

    def _abort(self) -> None:
        """Annule le travail non validé et remet le dépôt dans son état d'origine."""
        try:
            self.git.rollback()
        except GitClientError as e:
            logger.error(f"Rollback impossible pendant le nettoyage : {e}")
        if not self.work_branch:
            return
        try:
            ahead = self.git.count_commits_ahead(self.original_sha, self.work_branch)
            self._checkout_original()
            if ahead == 0:
                self.git.delete_branch(self.work_branch, force=True)
                self.work_branch = None
            else:
                self.branch_kept = True
                logger.warning(f"La branche '{self.work_branch}' contient des commits et est conservée.")
        except GitClientError as e:
            logger.error(f"Impossible de revenir sur la branche d'origine : {e}")

    def _restore_stash(self) -> None:
        if self.stashed and self.stash_restored is None:
            logger.info("Stash Guard : restauration des modifications locales mises en réserve...")
            self.stash_restored = self.git.stash_pop(self.stash_message)
