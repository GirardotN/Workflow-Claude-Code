"""
Orchestrateur multi-agents Claude & TypeSafe Jev.

Point d'entrée programmatique (`MultiAgentOrchestrator.run`) : choisit le mode (In-Repo ou Standalone),
fournit au moteur (`fsm.WorkflowEngine`) les services dont il dépend (appels Claude avec politique d'outils,
décisions Jev masquées et tracées, tests du projet, persistance) et gère le cycle de vie Git du mode In-Repo
(`isolation.IsolatedRun` : branche, commit, fusion, stash).
"""

import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import Callable, List, Optional

from . import doc_guard, prompts
from .clients.claude_cli import ClaudeCliClient, ClaudeCliError, ClaudeResult
from .clients.git_client import GitClient, GitClientError
from .clients.jev_client import JevClient
from .clients.test_runner import TestResult, TestRunner
from .commit_message import extract_commit_message
from .config import (
    DEFAULT_ALLOW_BASH,
    DEFAULT_DOC_EDIT,
    DEFAULT_RUN_TESTS,
    DEFAULT_USE_BRANCH,
    JEV_MAX_STATE_CHARS,
    JEV_SEND,
    MAX_DIFF_CHARS,
    MAX_RETRIES,
)
from .fsm import InRepoBackend, StandaloneBackend, WorkflowEngine, WorkflowMaxRetriesExceeded
from .isolation import IsolatedRun, WorkflowPreconditionError
from .jev_context import build_validation_context, parse_verdict, prepare_context
from .models import DevSpecialty, StepRecord, WorkflowExecutionReport
from .policy import ModelPolicy
from .prompts import COMPLEXITY_DESCRIPTIONS, SPECIALTY_DESCRIPTIONS, VERDICT_INSTRUCTION
from .roles import Role
from .text_utils import clean_code_output, normalize_test_output
from .tool_policy import policy_for
from .ui.terminal import Spinner

__all__ = [
    "COMPLEXITY_DESCRIPTIONS",
    "SPECIALTY_DESCRIPTIONS",
    "VERDICT_INSTRUCTION",
    "MultiAgentOrchestrator",
    "WorkflowMaxRetriesExceeded",
    "WorkflowPreconditionError",
    "clean_code_output",
    "normalize_test_output",
]

logger = logging.getLogger("orchestrator")

LANG_TO_EXT = {
    "python": ".py",
    "py": ".py",
    "typescript": ".ts",
    "ts": ".ts",
    "tsx": ".tsx",
    "javascript": ".js",
    "js": ".js",
    "jsx": ".jsx",
    "csharp": ".cs",
    "cs": ".cs",
    "c#": ".cs",
    "html": ".html",
    "css": ".css",
    "json": ".json",
    "sql": ".sql",
}


class MultiAgentOrchestrator:
    """
    Orchestre les interactions entre le CLI Claude Code et l'API décisionnelle TypeSafe Jev.
    """

    def __init__(
        self,
        claude_client: Optional[ClaudeCliClient] = None,
        jev_client: Optional[JevClient] = None,
        test_runner: Optional[TestRunner] = None,
        max_retries: int = MAX_RETRIES,
        workspace_dir: Optional[str] = None,
        project_dir: Optional[str] = None,
        standalone_mode: bool = False,
        auto_commit: bool = False,
        use_branch: bool = DEFAULT_USE_BRANCH,
        auto_merge: bool = False,
        allow_bash: bool = DEFAULT_ALLOW_BASH,
        run_tests: bool = DEFAULT_RUN_TESTS,
        on_step_callback: Optional[Callable[[StepRecord], None]] = None,
        on_merge_decision: Optional[Callable[[WorkflowExecutionReport], bool]] = None,
        jev_send: str = JEV_SEND,
        jev_max_chars: int = JEV_MAX_STATE_CHARS,
        max_prompt_chars: int = MAX_DIFF_CHARS,
        doc_edit: bool = DEFAULT_DOC_EDIT,
    ):
        self.claude = claude_client or ClaudeCliClient()
        self.jev = jev_client or JevClient()
        self.test_runner = test_runner or TestRunner()
        self.max_retries = max_retries
        self.workspace_dir = workspace_dir
        self.project_dir = Path(project_dir).resolve() if project_dir else None
        self.standalone_mode = standalone_mode
        self.auto_commit = auto_commit
        self.use_branch = use_branch
        self.auto_merge = auto_merge
        self.allow_bash = allow_bash
        self.run_tests = run_tests
        self.on_step_callback = on_step_callback
        # Appelé en fin de run In-Repo réussi (branche d'isolation committée, pas de --merge) :
        # retourne True pour fusionner dans la branche d'origine. Sans callback : pas de fusion.
        self.on_merge_decision = on_merge_decision
        self.git = GitClient(self.project_dir) if self.project_dir else None
        self._isolated_cwd: Optional[str] = None
        if jev_send not in ("full", "review-only"):
            raise ValueError(f"jev_send doit valoir 'full' ou 'review-only' (reçu : {jev_send!r})")
        self.jev_send = jev_send
        self.jev_max_chars = jev_max_chars
        self.max_prompt_chars = max_prompt_chars
        self.doc_edit = doc_edit

    # ------------------------------------------------------------------
    # Point d'entrée
    # ------------------------------------------------------------------
    def run(self, prompt_simple: str, raise_on_failure: bool = False) -> WorkflowExecutionReport:
        """
        Exécute le workflow multi-agents de bout en bout à partir d'un prompt utilisateur simple.
        Bascule automatiquement entre le mode In-Repo (projet existant sous Git)
        et le mode Standalone (génération de fichier neuf dans output/).
        """
        report = WorkflowExecutionReport(prompt_simple=prompt_simple)
        report.jev_mode = getattr(self.jev, "mode", "")

        # Détection du mode : In-Repo si project_dir est un dépôt Git valide et que standalone_mode n'est pas forcé
        is_in_repo = False
        if not self.standalone_mode and self.project_dir and self.git and self.git.is_git_repository():
            is_in_repo = True

        report.is_in_repo = is_in_repo
        report.project_dir = str(self.project_dir) if self.project_dir else None

        try:
            if is_in_repo:
                return self._run_in_repo(prompt_simple, report, raise_on_failure=raise_on_failure)
            return self._run_standalone(report, raise_on_failure=raise_on_failure)
        finally:
            self._cleanup_isolated_cwd()

    # ------------------------------------------------------------------
    # Appels à Claude
    # ------------------------------------------------------------------
    def _isolated_dir(self) -> str:
        """Répertoire temporaire vide : les agents « texte seul » n'y voient aucun fichier du projet."""
        if self._isolated_cwd is None or not Path(self._isolated_cwd).is_dir():
            self._isolated_cwd = tempfile.mkdtemp(prefix="workflow-isolated-")
        return self._isolated_cwd

    def _cleanup_isolated_cwd(self) -> None:
        if self._isolated_cwd:
            shutil.rmtree(self._isolated_cwd, ignore_errors=True)
            self._isolated_cwd = None

    def _call_claude(
        self,
        role: Role,
        prompt: str,
        model: str,
        cwd: Optional[str] = None,
        standalone: bool = False,
        append_system_prompt: Optional[str] = None,
    ) -> str:
        """
        Appel à Claude avec la politique d'outils du rôle (voir tool_policy.py) :
        - relecteurs (qualité, sécurité, feedback, doc) et mode Standalone : AUCUN outil, dans un
          répertoire vide → l'agent ne peut pas lire le dépôt, il ne voit que le prompt ;
        - spécification : lecture seule ; développement : édition (+ Bash restreint si --allow-bash).
        """
        if standalone or role.is_isolated_reviewer:
            options = {"cwd": self._isolated_dir(), "tools": "", "role": role}
        else:
            policy = policy_for(role, allow_bash=self.allow_bash)
            options = {"cwd": cwd, "tools": policy.tools, "role": role}
            if policy.permission_mode:
                options["permission_mode"] = policy.permission_mode
            if policy.allowed_tools:
                options["allowed_tools"] = policy.allowed_tools
            if policy.disallowed_tools:
                options["disallowed_tools"] = policy.disallowed_tools
        if append_system_prompt:
            options["append_system_prompt"] = append_system_prompt
        return self.claude.run(prompt, model=model, **options)

    # ------------------------------------------------------------------
    # Mode In-Repo
    # ------------------------------------------------------------------
    def _run_in_repo(
        self,
        prompt_simple: str,
        report: WorkflowExecutionReport,
        raise_on_failure: bool = False,
    ) -> WorkflowExecutionReport:
        """
        Exécute le workflow en mode In-Repo :
        1. Isolation sur branche temporaire dédiée (workflow/ai-*) après mise en réserve du travail en cours.
        2. Machine à états (spécification, routage Jev, dev in-situ, tests, qualité, sécurité, doc).
        3. Commit sémantique sur la branche d'isolation, puis retour sur la branche d'origine
           (fusion automatique ou sur décision), et seulement ensuite restauration du stash.
        """
        logger.info(f"Démarrage du workflow In-Repo dans : {self.project_dir} pour : '{prompt_simple[:60]}...'")

        # Stash Guard + branche d'isolation, avec nettoyage garanti sur toute sortie (voir isolation.py)
        guard = IsolatedRun(
            self.git,
            use_branch=self.use_branch,
            will_commit=self.use_branch or self.auto_commit,
        )
        try:
            with guard:
                report.original_branch = guard.original_branch or "HEAD"
                report.branch_name = guard.work_branch
                self._in_repo_flow(prompt_simple, report, guard, raise_on_failure)
        finally:
            self._apply_isolation_state(report, guard)
        return report

    @staticmethod
    def _apply_isolation_state(report: WorkflowExecutionReport, guard: IsolatedRun) -> None:
        """Reporte dans le rapport l'état final de l'isolation Git (branche, fusion, stash)."""
        if guard.merged:
            report.branch_name = report.original_branch
        elif guard.work_branch and guard.branch_kept:
            report.branch_name = guard.work_branch
        else:
            report.branch_name = None
        report.merged = guard.merged
        report.stash_restored = guard.stash_restored

    def _in_repo_flow(
        self,
        prompt_simple: str,
        report: WorkflowExecutionReport,
        guard: IsolatedRun,
        raise_on_failure: bool = False,
    ) -> None:
        """
        Exécuté à l'intérieur de `guard` : toute sortie sans appel à guard.leave() (échec, exception,
        Ctrl-C) annule le travail, revient sur la branche d'origine puis restaure le stash de l'utilisateur.
        """
        engine = WorkflowEngine(self, InRepoBackend(self), report, raise_on_failure)
        if not engine.run():
            return

        # -------------------------------------------------------------------------
        # DOCUMENTATION DU PROJET : l'agent doc met à jour la doc existante (hors code), sous garde-fou
        # -------------------------------------------------------------------------
        if self.doc_edit and guard.has_changes():
            self._doc_edit_step(report)

        # -------------------------------------------------------------------------
        # COMMIT : systématique sur la branche d'isolation (sans risque pour la branche de
        # l'utilisateur) ; sans branche, uniquement si --commit est demandé.
        # -------------------------------------------------------------------------
        should_commit = guard.work_branch is not None or self.auto_commit
        if should_commit and guard.has_changes():
            commit_message = extract_commit_message(report.doc_et_commit, prompt_simple)
            if "\n\n" not in commit_message:
                commit_message += f"\n\n{prompt_simple}"
            try:
                report.commit_hash = guard.commit(commit_message)
            except GitClientError as e:
                err = f"Échec de la création du commit : {e}"
                logger.error(err)
                report.error_message = err
                report.is_success = False
                # Le patch validé est conservé sur disque avant que IsolatedRun n'annule le travail.
                self._persist_patch(report)
                return
        elif should_commit:
            logger.warning("Aucune modification à committer : le dépôt est resté identique.")

        # -------------------------------------------------------------------------
        # FIN D'ISOLATION : retour sur la branche d'origine (fusion automatique ou sur
        # décision de l'utilisateur), AVANT la restauration du stash.
        # -------------------------------------------------------------------------
        merge = self.auto_merge
        if (
            report.commit_hash
            and guard.work_branch
            and guard.original_branch
            and not merge
            and self.on_merge_decision
        ):
            merge = bool(self.on_merge_decision(report))
        guard.leave(merge=merge)
        self._apply_isolation_state(report, guard)

        # Persistance disque si demandée
        if self.workspace_dir:
            self._persist_to_workspace(report)

        report.is_success = True
        logger.info(f"Workflow In-Repo complété avec succès en {report.iterations_count} itération(s) !")

    def _doc_edit_step(self, report: WorkflowExecutionReport) -> None:
        """
        Étape DOC_EDIT : un agent avec outils d'édition met à jour la documentation du projet ; `doc_guard`
        annule ensuite toute modification hors fichiers de documentation (le code validé reste intact).
        Étape « au mieux » : une erreur de Claude ici ne remet jamais en cause le code déjà validé.
        """
        model = ModelPolicy.for_type(report.workflow_type).doc
        prompt = prompts.doc_edit(report.git_diff, report.doc_et_commit, self.max_prompt_chars)
        snapshot = doc_guard.snapshot(self.git)

        logger.info(f">>> Mise à jour de la documentation du projet (Modèle: {model})")
        t0 = time.perf_counter()
        output = ""
        try:
            with Spinner(f"Mise à jour de la documentation du projet ({model})..."):
                output = self._call_claude(Role.DOC_EDIT, prompt, model, cwd=str(self.project_dir))
        except ClaudeCliError as e:
            logger.warning(f"Mise à jour de la documentation abandonnée ({e}) : le code validé est conservé.")
            output = f"[abandonnée] {e}"
        duration = time.perf_counter() - t0

        result = doc_guard.enforce(self.git, snapshot)  # toujours : l'agent a pu écrire avant d'échouer
        report.doc_files_kept = result.kept
        report.doc_files_reverted = result.reverted
        report.doc_diff = self.git.get_diff(paths=result.kept) if result.kept else ""
        report.modified_files = sorted(set(report.modified_files) | set(result.kept))

        summary = (
            f"Fichiers de documentation mis à jour : {', '.join(result.kept) or 'aucun'}\n"
            f"Modifications hors documentation annulées : {', '.join(result.reverted) or 'aucune'}\n\n{output}"
        )
        self._record_step(
            report, "DOC_EDIT", model, prompt, summary, duration,
            metadata={"kept": result.kept, "reverted": result.reverted},
        )

    # ------------------------------------------------------------------
    # Mode Standalone
    # ------------------------------------------------------------------
    def _run_standalone(self, report: WorkflowExecutionReport, raise_on_failure: bool = False) -> WorkflowExecutionReport:
        """Exécute le workflow en mode Standalone (génération d'un fichier neuf dans output/)."""
        logger.info(f"Démarrage du workflow Standalone pour : '{report.prompt_simple[:60]}...'")
        engine = WorkflowEngine(self, StandaloneBackend(self), report, raise_on_failure)
        if not engine.run():
            return report

        # Persistance disque si demandée
        if self.workspace_dir:
            self._persist_to_workspace(report)

        report.is_success = True
        logger.info(f"Workflow complété avec succès en {report.iterations_count} itération(s) !")
        return report

    # ------------------------------------------------------------------
    # Jev : contexte masqué/plafonné, trace des décisions, contrôle de cohérence
    # ------------------------------------------------------------------
    def _jev_secrets(self) -> list:
        """Valeurs exactes à ne jamais envoyer au service (la clé TypeSafe elle-même)."""
        key = getattr(self.jev, "api_key", "")
        return [key] if key else []

    def _record_decision(self, report: WorkflowExecutionReport, step: str, **extra) -> None:
        decision = getattr(self.jev, "last_decision", None)
        if decision is None or not hasattr(decision, "as_dict"):
            return
        report.decisions.append({"step": step, **decision.as_dict(), **extra})

    def _jev_classify(self, report, step, context, choices, question_label, descriptions=None) -> str:
        """Routage par Jev sur un contexte masqué et plafonné."""
        safe_context = prepare_context(context, self.jev_max_chars, self._jev_secrets())
        kwargs = {"descriptions": descriptions} if getattr(self.jev, "supports_descriptions", False) else {}
        choice = self.jev.classify(safe_context, choices, question_label=question_label, **kwargs)
        self._record_decision(report, step)
        return choice

    def _jev_validate(self, report, step, diff_text, review, review_label, criteria, diff_label="Git Diff") -> bool:
        """
        Validation binaire par Jev. Le diff est masqué (secrets) et plafonné ; avec jev_send="review-only"
        il n'est pas envoyé du tout. Un verdict de relecteur contradictoire avec Jev est journalisé.
        """
        context = build_validation_context(
            diff_text,
            review,
            review_label,
            mode=self.jev_send,
            max_chars=self.jev_max_chars,
            diff_label=diff_label,
            extra_secrets=self._jev_secrets(),
        )
        valid = self.jev.validate(context=context, criteria=criteria)

        reviewer_verdict = parse_verdict(review)
        extra = {}
        if reviewer_verdict is not None:
            extra = {"reviewer_verdict": "PASS" if reviewer_verdict else "FAIL", "consistent": reviewer_verdict == bool(valid)}
            if reviewer_verdict != bool(valid):
                logger.warning(
                    f"Incohérence à l'étape {step} : le relecteur conclut {extra['reviewer_verdict']} "
                    f"mais Jev {'valide' if valid else 'rejette'}. La décision de Jev est appliquée."
                )
        self._record_decision(report, step, **extra)
        return bool(valid)

    # ------------------------------------------------------------------
    # Services du moteur
    # ------------------------------------------------------------------
    def _run_tests_clean(self) -> Optional[TestResult]:
        """
        Exécute la suite de tests puis supprime les fichiers non suivis qu'elle a créés
        (caches, rapports de couverture...) afin qu'ils n'entrent ni dans le diff relu par
        les agents ni dans le commit. Les fichiers produits par l'agent de développement
        (déjà présents avant l'exécution) sont conservés.
        """
        before = set(self.git.untracked_files())
        result = self.test_runner.run_tests(self.project_dir)
        created = [path for path in self.git.untracked_files() if path not in before]
        if created:
            logger.info(f"{len(created)} fichier(s) non suivi(s) créé(s) par les tests supprimé(s) du dépôt.")
            self.git.remove_untracked(created)
        return result

    @staticmethod
    def excluded_files_note(excluded: List[str]) -> str:
        """Mention ajoutée au diff relu quand des fichiers générés/volumineux en ont été retirés."""
        if not excluded:
            return ""
        shown = ", ".join(excluded[:15]) + (f" (+{len(excluded) - 15})" if len(excluded) > 15 else "")
        return f"\n\n[{len(excluded)} fichier(s) généré(s) ou volumineux exclu(s) de ce diff : {shown}]"

    def _persist_patch(self, report: WorkflowExecutionReport) -> None:
        """Sauvegarde le patch courant (utile quand le commit échoue et que le travail va être annulé)."""
        if not self.workspace_dir:
            return
        try:
            ws_path = Path(self.workspace_dir)
            ws_path.mkdir(parents=True, exist_ok=True)
            patch_file = ws_path / "LATEST_PATCH.diff"
            patch_file.write_text(report.git_diff, encoding="utf-8")
            logger.warning(f"Patch conservé dans : {patch_file}")
        except OSError as e:
            logger.error(f"Impossible de sauvegarder le patch : {e}")

    def _record_step(
        self,
        report: WorkflowExecutionReport,
        step_name: str,
        model: str,
        prompt: str,
        output: str,
        duration: float,
        metadata: Optional[dict] = None,
    ):
        """Enregistre une étape exécutée pour la traçabilité complète (durée, coût et tours rapportés par Claude)."""
        meta = dict(metadata or {})
        if model != "test_runner":
            result = getattr(self.claude, "last_result", None)
            if isinstance(result, ClaudeResult):
                meta.update(
                    {
                        "cost_usd": result.cost_usd,
                        "num_turns": result.num_turns,
                        "claude_duration_ms": result.duration_ms,
                        "session_id": result.session_id,
                    }
                )
                report.cost_usd += result.cost_usd or 0.0
                self.claude.last_result = None  # consommé : ne jamais l'attribuer à l'étape suivante
        record = StepRecord(
            step_name=step_name,
            model=model,
            prompt_sent=prompt,
            output_received=output,
            duration_seconds=duration,
            metadata=meta,
        )
        report.history.append(record)
        if self.on_step_callback:
            try:
                self.on_step_callback(record)
            except Exception as e:
                logger.error(f"Erreur dans on_step_callback: {e}")

    def _persist_to_workspace(self, report: WorkflowExecutionReport):
        """Persiste les artefacts générés sur le disque du projet."""
        try:
            ws_path = Path(self.workspace_dir)
            ws_path.mkdir(parents=True, exist_ok=True)

            if report.is_in_repo:
                # Mode In-Repo : Sauvegarde du diff dans le workspace
                diff_file = ws_path / "LATEST_PATCH.diff"
                diff_file.write_text(report.git_diff, encoding="utf-8")
                logger.info(f"Patch git validé sauvegardé dans : {diff_file}")
            else:
                # Mode Standalone : Sauvegarde du code produit nettoyé de ses balises markdown
                cleaned_code, detected_lang = clean_code_output(report.code_produit)
                ext = LANG_TO_EXT.get(detected_lang) if detected_lang else None
                if not ext:
                    ext_map = {
                        DevSpecialty.PYTHON: ".py",
                        DevSpecialty.CSHARP: ".cs",
                        DevSpecialty.NODEJS: ".ts",
                        DevSpecialty.UI: ".tsx",
                    }
                    ext = ext_map.get(report.dev_specialty, ".txt")

                code_file = ws_path / f"generated_solution{ext}"
                code_file.write_text(cleaned_code, encoding="utf-8")
                logger.info(f"Code produit sauvegardé dans : {code_file}")

            # 1 bis. Diff de la seule documentation mise à jour dans le projet (séparé du patch de code)
            if report.doc_diff:
                (ws_path / "DOC_CHANGES.diff").write_text(report.doc_diff, encoding="utf-8")

            # 2. Sauvegarde de la documentation
            doc_file = ws_path / "GENERATED_DOC.md"
            doc_file.write_text(report.doc_et_commit, encoding="utf-8")
            logger.info(f"Documentation sauvegardée dans : {doc_file}")

            # 3. Sauvegarde du rapport d'audit
            report_file = ws_path / "WORKFLOW_AUDIT.md"
            lines = [
                "# Rapport d'Exécution Multi-Agents",
                f"- **Mode :** {'In-Repo (Modifications directes)' if report.is_in_repo else 'Standalone'}",
                f"- **Prompt Initial :** {report.prompt_simple}",
                f"- **Type de Workflow :** {report.workflow_type.value if report.workflow_type else 'N/A'}",
                f"- **Spécialité :** {report.dev_specialty.value if report.dev_specialty else 'N/A'}",
                f"- **Itérations :** {report.iterations_count}",
                f"- **Étapes exécutées :** {len(report.history)}",
                f"- **Jev :** {'SIMULATION (validations non fiables)' if report.jev_mode == 'mock' else 'API réelle' if report.jev_mode else 'N/A'}",
            ]
            if report.cost_usd:
                lines.append(f"- **Coût équivalent API (informatif) :** {report.cost_usd:.4f} USD")
            if report.is_in_repo:
                lines.append(f"- **Dépôt cible :** {report.project_dir}")
                lines.append(f"- **Fichiers modifiés :** {', '.join(report.modified_files) if report.modified_files else 'Aucun'}")
                if report.doc_files_kept:
                    lines.append(f"- **Documentation mise à jour :** {', '.join(report.doc_files_kept)}")
                if report.doc_files_reverted:
                    lines.append(f"- **Modifications hors documentation annulées :** {', '.join(report.doc_files_reverted)}")
                if report.commit_hash:
                    lines.append(f"- **Commit Git créé :** `{report.commit_hash}`")
            lines.extend(["", "## Historique des étapes :"])
            for step in report.history:
                lines.append(f"- **{step.step_name}** ({step.model}, {step.duration_seconds:.2f}s)")
            report_file.write_text("\n".join(lines), encoding="utf-8")
            logger.info(f"Rapport d'audit sauvegardé dans : {report_file}")

        except Exception as e:
            logger.error(f"Erreur lors de la persistance disque : {e}")
