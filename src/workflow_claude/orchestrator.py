"""
Moteur d'orchestration multi-agents Claude & TypeSafe Jev.
Implémente la machine à états finis, le routage déterministe,
l'isolation stricte des contextes et les garde-fous anti-dérive.
"""

import logging
import re
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

from .clients.claude_cli import ClaudeCliClient
from .clients.git_client import GitClient
from .clients.jev_client import JevClient
from .clients.test_runner import TestRunner
from .config import (
    DEFAULT_ALLOW_BASH,
    DEFAULT_ALLOW_DIRTY,
    DEFAULT_RUN_TESTS,
    DEFAULT_USE_BRANCH,
    MAX_RETRIES,
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
)
from .models import (
    DevSpecialty,
    StepRecord,
    WorkflowExecutionReport,
    WorkflowType,
)
from .ui.terminal import Spinner

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


class WorkflowMaxRetriesExceeded(RuntimeError):
    """Exception levée lorsque le nombre maximum de boucles de feedback est atteint."""
    pass


class MultiAgentOrchestrator:
    """
    Machine à états orchestrant les interactions entre le CLI Claude Code
    et l'API décisionnelle TypeSafe Jev.
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
        allow_dirty: bool = DEFAULT_ALLOW_DIRTY,
        on_step_callback: Optional[Callable[[StepRecord], None]] = None,
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
        self.allow_dirty = allow_dirty
        self.on_step_callback = on_step_callback
        self.git = GitClient(self.project_dir) if self.project_dir else None

    def run(self, prompt_simple: str, raise_on_failure: bool = False) -> WorkflowExecutionReport:
        """
        Exécute le workflow multi-agents de bout en bout à partir d'un prompt utilisateur simple.
        Bascule automatiquement entre le mode In-Repo (projet existant sous Git)
        et le mode Standalone (génération de fichier neuf dans output/).
        """
        report = WorkflowExecutionReport(prompt_simple=prompt_simple)

        # Détection du mode : In-Repo si project_dir est un dépôt Git valide et que standalone_mode n'est pas forcé
        is_in_repo = False
        if not self.standalone_mode and self.project_dir and self.git and self.git.is_git_repository():
            is_in_repo = True

        report.is_in_repo = is_in_repo
        report.project_dir = str(self.project_dir) if self.project_dir else None

        if is_in_repo:
            return self._run_in_repo(prompt_simple, report, raise_on_failure=raise_on_failure)
        else:
            return self._run_standalone(prompt_simple, report, raise_on_failure=raise_on_failure)

    def _run_in_repo(
        self,
        prompt_simple: str,
        report: WorkflowExecutionReport,
        raise_on_failure: bool = False,
    ) -> WorkflowExecutionReport:
        """
        Exécute le workflow en mode In-Repo :
        1. Isolation sur branche temporaire dédiée (workflow/ai-*).
        2. Exploration et localisation ciblée (filtrage node_modules, .git, .venv).
        3. Aiguillage Jev (complexité & spécialité).
        4. Développement in-situ modifiant directement les fichiers (outils sandboxés).
        5. Boucle de tests automatisée (Oracle de vérité) avec auto-correction si échec.
        6. Capture du git diff réel.
        7. Check Qualité sur le git diff (Jev validation/rejet + rollback git si rejet).
        8. Check Sécurité sur le git diff (Jev validation/rejet + rollback git si rejet).
        9. Commit sémantique et fusion optionnelle sur la branche d'origine.
        """
        logger.info(f"Démarrage du workflow In-Repo dans : {self.project_dir} pour : '{prompt_simple[:60]}...'")

        # -------------------------------------------------------------------------
        # STASH GUARD : SÉCURITÉ ANTI-PERTE DE DONNÉES
        # -------------------------------------------------------------------------
        stashed = False
        if not self.git.is_working_tree_clean():
            if not self.allow_dirty:
                logger.info("Stash Guard : Modifications locales non commitées détectées. Mise en réserve automatique via git stash...")
                stashed = self.git.stash_push(f"workflow-auto-stash-{int(time.time())}")
            else:
                logger.warning("Attention : Le répertoire de travail n'est pas propre et --allow-dirty est actif.")

        try:
            # -------------------------------------------------------------------------
            # BASELINE DES TESTS (CYCLE 0) : ÉTAT DE SANTÉ INITIAL DU PROJET
            # -------------------------------------------------------------------------
            baseline_failed = False
            baseline_output = ""
            if self.run_tests and self.test_runner:
                with Spinner("Vérification de la santé initiale des tests (Baseline)..."):
                    baseline_res = self.test_runner.run_tests(self.project_dir)
                if baseline_res is not None:
                    report.baseline_tests_passed = baseline_res.passed
                    if not baseline_res.passed:
                        baseline_failed = True
                        baseline_output = baseline_res.output
                        logger.warning(
                            f"⚠️ BASELINE : La suite de tests échoue DÉJÀ avant toute modification ({baseline_res.command}). "
                            "Les échecs préexistants ne seront pas considérés comme des régressions de Claude."
                        )

            # -------------------------------------------------------------------------
            # ISOLATION TRANSACTIONNELLE PAR BRANCHE GIT
            # -------------------------------------------------------------------------
            original_branch = "HEAD"
            work_branch = None
            if self.use_branch and self.git:
                original_branch = self.git.get_current_branch()
                report.original_branch = original_branch
                timestamp = int(time.time())
                work_branch = f"workflow/ai-{timestamp}"
                try:
                    self.git.create_and_checkout_branch(work_branch)
                    report.branch_name = work_branch
                    logger.info(f"Isolation Git active : branche '{work_branch}' créée depuis '{original_branch}'.")
                except Exception as e:
                    logger.warning(f"Impossible d'isoler sur une branche dédiée ({e}), travail sur {original_branch}")
                    work_branch = None

            # =========================================================================
            # ÉTAPE 1 : Exploration du Codebase & Spécification In-Situ (Sonnet)
            # =========================================================================
            logger.info(">>> Étape 1 : Exploration du codebase et spécification technique in-situ (Sonnet)")
            spec_prompt = (
                "Tu es un Lead Software Architect. Le projet cible se trouve dans le répertoire courant.\n"
                f"Demande utilisateur : {prompt_simple}\n\n"
                "Explore le codebase à l'aide de tes outils pour localiser précisément les fichiers, "
                "fonctions ou composants concernés par cette demande.\n"
                "IMPORTANT : Ignore impérativement les répertoires et artefacts volumineux ou générés : "
                "node_modules, .git, dist, build, .venv, venv, __pycache__, .pytest_cache, coverage, bin, obj.\n\n"
                "Rédige une spécification technique d'implémentation in-situ complète :\n"
                "1. Fichiers et fonctions cibles identifiés (chemins relatifs précis)\n"
                "2. Analyse de l'implémentation actuelle\n"
                "3. Plan d'édition chirurgicale requis\n"
                "4. Contrats d'interfaces et critères de non-régression."
            )
            t0 = time.perf_counter()
            with Spinner("Exploration du codebase et rédaction de la spécification in-situ..."):
                spec_complexe = self.claude.run(
                    spec_prompt,
                    model=MODEL_SONNET,
                    cwd=str(self.project_dir),
                    tools="Read,Grep,Glob",
                )
            dur = time.perf_counter() - t0
            report.spec_complexe = spec_complexe
            self._record_step(report, "1_EXPLORATION_ET_SPEC", MODEL_SONNET, spec_prompt, spec_complexe, dur)

            # =========================================================================
            # ÉTAPE 2 : Aiguillage de complexité et spécialité par JEV
            # =========================================================================
            logger.info(">>> Étape 2 : Aiguillage de complexité et spécialité dev via Jev")
            workflow_type_str = self.jev.classify(
                spec_complexe,
                [WorkflowType.SIMPLE.value, WorkflowType.MOYENNE.value, WorkflowType.COMPLEXE.value],
                question_label="Quel est le niveau de complexité de cette tâche de refactoring / modification ?",
            )
            report.workflow_type = WorkflowType.from_str(workflow_type_str)

            dev_specialty_str = self.jev.classify(
                spec_complexe,
                [
                    DevSpecialty.CSHARP.value,
                    DevSpecialty.NODEJS.value,
                    DevSpecialty.UI.value,
                    DevSpecialty.PYTHON.value,
                ],
                question_label="Quelle est la spécialité technique du développeur requise pour ce projet ?",
            )
            report.dev_specialty = DevSpecialty.from_str(dev_specialty_str)

            logger.info(f"Aiguillage Jev validé : Workflow={report.workflow_type.value}, Dev={report.dev_specialty.value}")

            is_complexe = (report.workflow_type == WorkflowType.COMPLEXE)
            is_simple = (report.workflow_type == WorkflowType.SIMPLE)

            dev_model = MODEL_OPUS if is_complexe else MODEL_SONNET
            quality_model = MODEL_OPUS if is_complexe else MODEL_SONNET
            feedback_bug_model = MODEL_SONNET if is_complexe else MODEL_HAIKU
            security_model = MODEL_SONNET
            feedback_secu_model = MODEL_SONNET if is_complexe else MODEL_HAIKU
            doc_commit_model = MODEL_SONNET if is_simple else MODEL_HAIKU

            # Configuration des outils pour l'agent de développement (sandbox Bash)
            dev_tools = "Read,Edit,Write,Grep,Glob"
            if self.allow_bash:
                dev_tools += ",Bash"

            # =========================================================================
            # MACHINE À ÉTATS IN-REPO : DEV IN-SITU <-> TESTS <-> QUALITÉ (<-> SÉCURITÉ)
            # =========================================================================
            etape = "DEV"
            diff_content = ""
            review_qualite = ""
            review_securite = ""
            dernier_feedback = ""
            iter_count = 0

            while etape != "DOC_ET_COMMIT":
                if iter_count >= self.max_retries:
                    err = (
                        f"Circuit breaker : Nombre maximum d'itérations ({self.max_retries}) atteint. "
                        f"Arrêt forcé à l'étape '{etape}' pour prévenir une consommation incontrôlée."
                    )
                    logger.error(err)
                    report.error_message = err
                    report.is_success = False

                    # Restauration de la branche d'origine si isolation active
                    if work_branch and self.git:
                        logger.warning(f"Restauration de la branche d'origine '{original_branch}'...")
                        self.git.rollback()
                        self.git.checkout_branch(original_branch)
                        self.git.delete_branch(work_branch, force=True)

                    if raise_on_failure:
                        raise WorkflowMaxRetriesExceeded(err)
                    return report

                # ---------------------------------------------------------------------
                # Nœud : DÉVELOPPEMENT IN-SITU
                # ---------------------------------------------------------------------
                if etape == "DEV":
                    iter_count += 1
                    report.iterations_count = iter_count
                    logger.info(f"--- Cycle {iter_count}/{self.max_retries} : DÉVELOPPEMENT IN-SITU (Modèle: {dev_model}) ---")

                    if not dernier_feedback:
                        dev_prompt = (
                            f"Tu es un expert {report.dev_specialty.value}.\n"
                            "Applique directement les modifications demandées dans les fichiers du projet "
                            "conformément au cahier des charges ci-dessous :\n\n"
                            f"CAHIER DES CHARGES :\n{spec_complexe}\n\n"
                            "Utilise tes outils pour modifier chirurgicalement les fichiers en place. "
                            "Ne modifie que ce qui est strictement nécessaire et respecte l'architecture existante."
                        )
                    else:
                        dev_prompt = (
                            f"Tu es un expert {report.dev_specialty.value}.\n"
                            "La tentative précédente a rencontré un problème ou a été rejetée. "
                            "Corrige le tir directement dans les fichiers du projet :\n\n"
                            f"CAHIER DES CHARGES :\n{spec_complexe}\n\n"
                            f"RETOURS OBLIGATOIRES À CORRIGER :\n{dernier_feedback}\n\n"
                            "Applique les corrections nécessaires dans les fichiers du projet."
                        )

                    t0 = time.perf_counter()
                    with Spinner(f"Développement chirurgical in-situ ({dev_model})..."):
                        self.claude.run(
                            dev_prompt,
                            model=dev_model,
                            cwd=str(self.project_dir),
                            tools=dev_tools,
                            permission_mode="acceptEdits",
                        )
                    dur = time.perf_counter() - t0

                    # Capture du diff réel dans le dépôt Git (y compris nouveaux fichiers untracked via intent-to-add)
                    diff_content = self.git.get_diff()
                    modified_files = self.git.get_modified_files()
                    report.git_diff = diff_content
                    report.modified_files = modified_files
                    report.code_produit = diff_content

                    self._record_step(
                        report,
                        f"DEV_IN_SITU_CYCLE_{iter_count}",
                        dev_model,
                        dev_prompt,
                        f"Fichiers modifiés: {', '.join(modified_files)}\n\nGit Diff:\n{diff_content}",
                        dur,
                    )

                    dernier_feedback = ""

                    # -----------------------------------------------------------------
                    # Nœud intermédiaire : BOUCLE DE TESTS AUTOMATISÉE (Oracle)
                    # -----------------------------------------------------------------
                    if self.run_tests and self.test_runner:
                        with Spinner("Exécution de la suite de tests du projet..."):
                            test_res = self.test_runner.run_tests(self.project_dir)
                        if test_res is not None:
                            report.tests_passed = test_res.passed
                            report.tests_output = test_res.output
                            is_new_failure = not (
                                baseline_failed
                                and normalize_test_output(test_res.output) == normalize_test_output(baseline_output)
                            )
                            if not test_res.passed and is_new_failure:
                                logger.warning(f">>> Échec des tests du projet ({test_res.command}). Déclenchement de l'auto-correction...")
                                dernier_feedback = (
                                    f"La modification apportée a provoqué des régressions ou des échecs dans les tests du projet.\n"
                                    f"COMMANDE DE TEST : {test_res.command}\n\n"
                                    f"TRACE D'ERREUR DES TESTS :\n{test_res.output}\n\n"
                                    "Corrige immédiatement le code dans les fichiers du projet pour que les tests réussissent."
                                )
                                self._record_step(
                                    report,
                                    f"TESTS_FAILED_CYCLE_{iter_count}",
                                    "test_runner",
                                    test_res.command,
                                    test_res.output,
                                    test_res.duration_seconds,
                                )
                                etape = "DEV"
                                continue
                            else:
                                logger.info(f">>> Succès de la suite de tests ({test_res.command}) en {test_res.duration_seconds:.2f}s.")
                                self._record_step(
                                    report,
                                    f"TESTS_PASSED_CYCLE_{iter_count}",
                                    "test_runner",
                                    test_res.command,
                                    f"Tests passés avec succès :\n{test_res.output[:300]}",
                                    test_res.duration_seconds,
                                )

                    etape = "CHECK_QUALITE"

                # ---------------------------------------------------------------------
                # Nœud : CHECK QUALITÉ SUR GIT DIFF
                # ---------------------------------------------------------------------
                elif etape == "CHECK_QUALITE":
                    logger.info(f"--- Cycle {iter_count} : CHECK QUALITÉ SUR GIT DIFF (Modèle: {quality_model}) ---")
                    quality_prompt = (
                        "Tu es un Senior Code Reviewer. Analyse uniquement le git diff ci-dessous représentant "
                        "les modifications apportées au projet pour évaluer la qualité, la robustesse, "
                        "l'absence de régression et le respect des conventions existantes. Sois intraitable sur les bugs :\n\n"
                        f"GIT DIFF :\n{diff_content}"
                    )
                    t0 = time.perf_counter()
                    with Spinner(f"Audit Qualité sur le git diff ({quality_model})..."):
                        review_qualite = self.claude.run(quality_prompt, model=quality_model)
                    dur = time.perf_counter() - t0
                    report.review_qualite = review_qualite
                    self._record_step(
                        report,
                        f"CHECK_QUALITE_DIFF_CYCLE_{iter_count}",
                        quality_model,
                        quality_prompt,
                        review_qualite,
                        dur,
                    )

                    logger.info("Validation Qualité soumise à Jev (noul/binary)...")
                    decision_qualite = self.jev.validate(
                        context=f"Git Diff :\n{diff_content}\n\nReview Qualité :\n{review_qualite}",
                        criteria="Ce diff git est-il exempt de bugs, robuste et conforme aux critères de qualité logicielle ?",
                    )

                    if not decision_qualite:
                        logger.warning(">>> Jev : REJET Qualité/Bug détecté sur le diff. Rollback Git en cours...")
                        self.git.rollback()
                        fb_prompt = (
                            "Rédige un feedback correctif direct, concis et actionnable sous forme "
                            "de liste à puces (bullet points) sans bavardage, à partir de cette review de qualité :\n\n"
                            f"{review_qualite}"
                        )
                        t0 = time.perf_counter()
                        dernier_feedback = self.claude.run(fb_prompt, model=feedback_bug_model)
                        dur = time.perf_counter() - t0
                        self._record_step(
                            report,
                            f"FEEDBACK_QUALITE_CYCLE_{iter_count}",
                            feedback_bug_model,
                            fb_prompt,
                            dernier_feedback,
                            dur,
                        )
                        etape = "DEV"
                    else:
                        logger.info(">>> Jev : VALIDATION Qualité accordée sur le diff.")
                        if is_simple:
                            etape = "DOC_ET_COMMIT"
                        else:
                            etape = "CHECK_SECURITE"

                # ---------------------------------------------------------------------
                # Nœud : CHECK SÉCURITÉ SUR GIT DIFF
                # ---------------------------------------------------------------------
                elif etape == "CHECK_SECURITE":
                    logger.info(f"--- Cycle {iter_count} : CHECK SÉCURITÉ SUR GIT DIFF (Modèle: {security_model}) ---")
                    secu_prompt = (
                        "Tu es un Expert en Cyber-Sécurité logicielle (AppSec). Analyse en profondeur "
                        "ce git diff ainsi que sa review qualité préalable. Détecte toute vulnérabilité potentielle "
                        "(injections, failles logiques, fuite de données, gestion non sécurisée des secrets, régressions) :\n\n"
                        f"GIT DIFF :\n{diff_content}\n\n"
                        f"REVIEW QUALITÉ PRÉALABLE :\n{review_qualite}"
                    )
                    t0 = time.perf_counter()
                    with Spinner(f"Audit Cyber-Sécurité sur le git diff ({security_model})..."):
                        review_securite = self.claude.run(secu_prompt, model=security_model)
                    dur = time.perf_counter() - t0
                    report.review_securite = review_securite
                    self._record_step(
                        report,
                        f"CHECK_SECU_DIFF_CYCLE_{iter_count}",
                        security_model,
                        secu_prompt,
                        review_securite,
                        dur,
                    )

                    logger.info("Validation Sécurité soumise à Jev (noul/binary)...")
                    decision_secu = self.jev.validate(
                        context=f"Git Diff :\n{diff_content}\n\nReview Sécurité :\n{review_securite}",
                        criteria="Le code modifié dans ce git diff est-il sécurisé et exempt de toute vulnérabilité de sécurité ?",
                    )

                    if not decision_secu:
                        logger.warning(">>> Jev : REJET Sécurité détecté sur le diff. Rollback Git en cours...")
                        self.git.rollback()
                        fb_secu_prompt = (
                            "Rédige un feedback correctif de sécurité direct, concis et impératif sous "
                            "forme de liste à puces (bullet points) à partir de cet audit de sécurité :\n\n"
                            f"{review_securite}"
                        )
                        t0 = time.perf_counter()
                        dernier_feedback = self.claude.run(fb_secu_prompt, model=feedback_secu_model)
                        dur = time.perf_counter() - t0
                        self._record_step(
                            report,
                            f"FEEDBACK_SECU_CYCLE_{iter_count}",
                            feedback_secu_model,
                            fb_secu_prompt,
                            dernier_feedback,
                            dur,
                        )
                        etape = "DEV"
                    else:
                        logger.info(">>> Jev : VALIDATION Sécurité accordée sur le diff.")
                        etape = "DOC_ET_COMMIT"

            # =========================================================================
            # ÉTAPE FINALE : Documentation et proposition de Commit Git
            # =========================================================================
            logger.info(f">>> Étape Finale : Création de la doc et du commit conventionnel (Modèle: {doc_commit_model})")
            doc_prompt = (
                "Tu es un Technical Writer & Git Master. À partir de ce git diff définitivement validé dans le projet, génère :\n"
                "1. Une documentation technique concise des modifications apportées (fichiers touchés, comportement changé).\n"
                "2. Un message de commit Git conventionnel complet (type(scope): subject, corps explicatif).\n\n"
                f"GIT DIFF VALIDÉ :\n{diff_content}"
            )
            t0 = time.perf_counter()
            with Spinner(f"Génération de la documentation et du commit ({doc_commit_model})..."):
                doc_commit = self.claude.run(doc_prompt, model=doc_commit_model)
            dur = time.perf_counter() - t0
            report.doc_et_commit = doc_commit
            self._record_step(report, "FINAL_DOC_ET_COMMIT", doc_commit_model, doc_prompt, doc_commit, dur)

            # Application du commit automatique si demandée
            if self.auto_commit and diff_content:
                commit_subject = prompt_simple
                for line in doc_commit.splitlines():
                    line_s = line.strip()
                    if line_s.startswith(("feat", "fix", "refactor", "chore", "perf", "style", "test", "docs")):
                        commit_subject = line_s.strip("`*#")
                        break
                commit_hash = self.git.commit(f"{commit_subject}\n\n{prompt_simple}")
                report.commit_hash = commit_hash

                # Fusion automatique sur la branche d'origine si demandée
                if work_branch and self.auto_merge and original_branch != "HEAD":
                    logger.info(f"Fusion automatique de la branche {work_branch} dans {original_branch}...")
                    self.git.checkout_branch(original_branch)
                    merged = self.git.merge_branch(work_branch)
                    if merged:
                        self.git.delete_branch(work_branch)
                        report.branch_name = original_branch

            # Persistance disque si demandée
            if self.workspace_dir:
                self._persist_to_workspace(report)

            report.is_success = True
            logger.info(f"Workflow In-Repo complété avec succès en {iter_count} itération(s) !")
            return report

        finally:
            # Restauration garantie des modifications locales de l'utilisateur par Stash Guard
            if stashed and self.git:
                logger.info("Stash Guard : Restauration des modifications locales mises en réserve...")
                self.git.stash_pop()

    def _run_standalone(
        self,
        prompt_simple: str,
        report: WorkflowExecutionReport,
        raise_on_failure: bool = False,
    ) -> WorkflowExecutionReport:
        """
        Exécute le workflow en mode Standalone (génération d'un fichier neuf dans output/).
        """
        logger.info(f"Démarrage du workflow Standalone pour : '{prompt_simple[:60]}...'")

        # =========================================================================
        # ÉTAPE 1 : Création du prompt complexe / Spécification technique (Sonnet)
        # =========================================================================
        logger.info(">>> Étape 1 : Génération de la spécification technique (Sonnet)")
        spec_prompt = (
            "Tu es un Lead Software Architect. À partir de la demande succincte suivante, "
            "génère une spécification technique d'implémentation complète, claire et structurée "
            "(objectifs, architecture, contrats d'interface, cas d'erreurs, critères d'acceptation) :\n\n"
            f"Demande : {prompt_simple}"
        )
        t0 = time.perf_counter()
        spec_complexe = self.claude.run(spec_prompt, model=MODEL_SONNET)
        duration = time.perf_counter() - t0
        report.spec_complexe = spec_complexe
        self._record_step(
            report,
            "1_GENERATION_SPEC",
            MODEL_SONNET,
            spec_prompt,
            spec_complexe,
            duration,
        )

        # =========================================================================
        # ÉTAPE 2 : Choix du workflow et de la spécialité par JEV
        # =========================================================================
        logger.info(">>> Étape 2 : Aiguillage de complexité et spécialité dev via Jev")
        workflow_type_str = self.jev.classify(
            spec_complexe,
            [WorkflowType.SIMPLE.value, WorkflowType.MOYENNE.value, WorkflowType.COMPLEXE.value],
            question_label="Quel est le niveau de complexité de cette tâche ?",
        )
        report.workflow_type = WorkflowType.from_str(workflow_type_str)

        dev_specialty_str = self.jev.classify(
            spec_complexe,
            [
                DevSpecialty.CSHARP.value,
                DevSpecialty.NODEJS.value,
                DevSpecialty.UI.value,
                DevSpecialty.PYTHON.value,
            ],
            question_label="Quelle est la spécialité technique du développeur requise ?",
        )
        report.dev_specialty = DevSpecialty.from_str(dev_specialty_str)

        logger.info(f"Aiguillage Jev validé : Workflow={report.workflow_type.value}, Dev={report.dev_specialty.value}")

        is_complexe = (report.workflow_type == WorkflowType.COMPLEXE)
        is_simple = (report.workflow_type == WorkflowType.SIMPLE)

        dev_model = MODEL_OPUS if is_complexe else MODEL_SONNET
        quality_model = MODEL_OPUS if is_complexe else MODEL_SONNET
        feedback_bug_model = MODEL_SONNET if is_complexe else MODEL_HAIKU
        security_model = MODEL_SONNET
        feedback_secu_model = MODEL_SONNET if is_complexe else MODEL_HAIKU
        doc_commit_model = MODEL_SONNET if is_simple else MODEL_HAIKU

        # =========================================================================
        # MACHINE À ÉTATS STANDALONE : DEV <-> QUALITÉ (<-> SÉCURITÉ)
        # =========================================================================
        etape = "DEV"
        code_produit = ""
        review_qualite = ""
        review_securite = ""
        dernier_feedback = ""
        iter_count = 0

        while etape != "DOC_ET_COMMIT":
            if iter_count >= self.max_retries:
                err = (
                    f"Circuit breaker : Nombre maximum d'itérations ({self.max_retries}) atteint. "
                    f"Arrêt forcé à l'étape '{etape}' pour prévenir une consommation incontrôlée."
                )
                logger.error(err)
                report.error_message = err
                report.code_produit = code_produit
                report.is_success = False
                if raise_on_failure:
                    raise WorkflowMaxRetriesExceeded(err)
                return report

            # ---------------------------------------------------------------------
            # Nœud : DÉVELOPPEMENT
            # ---------------------------------------------------------------------
            if etape == "DEV":
                iter_count += 1
                report.iterations_count = iter_count
                logger.info(f"--- Cycle {iter_count}/{self.max_retries} : DÉVELOPPEMENT (Modèle: {dev_model}) ---")

                if not dernier_feedback:
                    dev_prompt = (
                        f"Tu es un expert {report.dev_specialty.value}.\n"
                        f"Implémente la solution complète et rigoureuse répondant au cahier des charges ci-dessous.\n\n"
                        f"CAHIER DES CHARGES :\n{spec_complexe}\n\n"
                        "Fournis le code complet prêt pour la production."
                    )
                else:
                    dev_prompt = (
                        f"Tu es un expert {report.dev_specialty.value}.\n"
                        f"Corrige et améliore le code existant pour résoudre rigoureusement les retours formulés ci-dessous.\n\n"
                        f"CAHIER DES CHARGES :\n{spec_complexe}\n\n"
                        f"CODE ACTUEL :\n{code_produit}\n\n"
                        f"RETOURS OBLIGATOIRES À CORRIGER :\n{dernier_feedback}\n\n"
                        "Fournis la nouvelle version complète et corrigée du code."
                    )

                t0 = time.perf_counter()
                code_produit = self.claude.run(dev_prompt, model=dev_model)
                dur = time.perf_counter() - t0
                report.code_produit = code_produit
                self._record_step(
                    report,
                    f"DEV_CYCLE_{iter_count}",
                    dev_model,
                    dev_prompt,
                    code_produit,
                    dur,
                )

                dernier_feedback = ""
                etape = "CHECK_QUALITE"

            # ---------------------------------------------------------------------
            # Nœud : CHECK BUG / QUALITÉ
            # ---------------------------------------------------------------------
            elif etape == "CHECK_QUALITE":
                logger.info(f"--- Cycle {iter_count} : CHECK QUALITÉ (Modèle: {quality_model}) ---")
                quality_prompt = (
                    "Tu es un Senior Code Reviewer. Analyse uniquement le code fourni ci-dessous "
                    "pour évaluer la qualité, la robustesse, la conformité aux bonnes pratiques "
                    "et détecter tout bug ou régression potentielle. Sois intraitable sur les bugs :\n\n"
                    f"CODE À ANALYSER :\n{code_produit}"
                )
                t0 = time.perf_counter()
                review_qualite = self.claude.run(quality_prompt, model=quality_model)
                dur = time.perf_counter() - t0
                report.review_qualite = review_qualite
                self._record_step(
                    report,
                    f"CHECK_QUALITE_CYCLE_{iter_count}",
                    quality_model,
                    quality_prompt,
                    review_qualite,
                    dur,
                )

                logger.info("Validation Qualité / Bug soumise à Jev (noul/binary)...")
                decision_qualite = self.jev.validate(
                    context=f"Code :\n{code_produit}\n\nReview Qualité :\n{review_qualite}",
                    criteria="Le code est-il exempt de bugs, robuste et conforme aux critères de qualité logicielle ?",
                )

                if not decision_qualite:
                    logger.warning(">>> Jev : REJET Qualité/Bug détecté.")
                    fb_prompt = (
                        "Rédige un feedback correctif direct, concis et actionnable sous forme "
                        "de liste à puces (bullet points) sans bavardage, à partir de cette review de qualité :\n\n"
                        f"{review_qualite}"
                    )
                    t0 = time.perf_counter()
                    dernier_feedback = self.claude.run(fb_prompt, model=feedback_bug_model)
                    dur = time.perf_counter() - t0
                    self._record_step(
                        report,
                        f"FEEDBACK_QUALITE_CYCLE_{iter_count}",
                        feedback_bug_model,
                        fb_prompt,
                        dernier_feedback,
                        dur,
                    )
                    etape = "DEV"
                else:
                    logger.info(">>> Jev : VALIDATION Qualité/Bug accordée.")
                    if is_simple:
                        etape = "DOC_ET_COMMIT"
                    else:
                        etape = "CHECK_SECURITE"

            # ---------------------------------------------------------------------
            # Nœud : CHECK SÉCURITÉ
            # ---------------------------------------------------------------------
            elif etape == "CHECK_SECURITE":
                logger.info(f"--- Cycle {iter_count} : CHECK SÉCURITÉ (Modèle: {security_model}) ---")
                secu_prompt = (
                    "Tu es un Expert en Cyber-Sécurité logicielle (AppSec). Analyse en profondeur "
                    "ce code ainsi que sa review qualité préalable. Détecte les vulnérabilités potentielles "
                    "(injections, failles logiques, fuites de données, gestion non sécurisée des secrets, déni de service) :\n\n"
                    f"CODE PRODUIT :\n{code_produit}\n\n"
                    f"REVIEW QUALITÉ PRÉALABLE :\n{review_qualite}"
                )
                t0 = time.perf_counter()
                review_securite = self.claude.run(secu_prompt, model=security_model)
                dur = time.perf_counter() - t0
                report.review_securite = review_securite
                self._record_step(
                    report,
                    f"CHECK_SECU_CYCLE_{iter_count}",
                    security_model,
                    secu_prompt,
                    review_securite,
                    dur,
                )

                logger.info("Validation Sécurité soumise à Jev (noul/binary)...")
                decision_secu = self.jev.validate(
                    context=f"Code :\n{code_produit}\n\nReview Sécurité :\n{review_securite}",
                    criteria="Le code est-il sécurisé et exempt de toute vulnérabilité de sécurité ?",
                )

                if not decision_secu:
                    logger.warning(">>> Jev : REJET Sécurité détecté.")
                    fb_secu_prompt = (
                        "Rédige un feedback correctif de sécurité direct, concis et impératif sous "
                        "forme de liste à puces (bullet points) à partir de cet audit de sécurité :\n\n"
                        f"{review_securite}"
                    )
                    t0 = time.perf_counter()
                    dernier_feedback = self.claude.run(fb_secu_prompt, model=feedback_secu_model)
                    dur = time.perf_counter() - t0
                    self._record_step(
                        report,
                        f"FEEDBACK_SECU_CYCLE_{iter_count}",
                        feedback_secu_model,
                        fb_secu_prompt,
                        dernier_feedback,
                        dur,
                    )
                    etape = "DEV"
                else:
                    logger.info(">>> Jev : VALIDATION Sécurité accordée.")
                    etape = "DOC_ET_COMMIT"

        # =========================================================================
        # ÉTAPE FINALE : Documentation et proposition de Commit Git
        # =========================================================================
        logger.info(f">>> Étape Finale : Création de la doc et du commit (Modèle: {doc_commit_model})")
        doc_prompt = (
            "Tu es un Technical Writer & Git Master. À partir de ce code définitivement validé, "
            "génère :\n"
            "1. Une documentation technique concise (description, usage, prérequis).\n"
            "2. Un message de commit Git conventionnel complet (type(scope): subject, corps explicatif).\n\n"
            f"CODE VALIDÉ :\n{code_produit}"
        )
        t0 = time.perf_counter()
        doc_commit = self.claude.run(doc_prompt, model=doc_commit_model)
        dur = time.perf_counter() - t0
        report.doc_et_commit = doc_commit
        self._record_step(
            report,
            "FINAL_DOC_ET_COMMIT",
            doc_commit_model,
            doc_prompt,
            doc_commit,
            dur,
        )

        # Persistance disque si demandée
        if self.workspace_dir:
            self._persist_to_workspace(report)

        report.is_success = True
        logger.info(f"Workflow complété avec succès en {iter_count} itération(s) !")
        return report

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
        """Enregistre une étape exécutée pour la traçabilité complète."""
        record = StepRecord(
            step_name=step_name,
            model=model,
            prompt_sent=prompt,
            output_received=output,
            duration_seconds=duration,
            metadata=metadata or {},
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
            ]
            if report.is_in_repo:
                lines.append(f"- **Dépôt cible :** {report.project_dir}")
                lines.append(f"- **Fichiers modifiés :** {', '.join(report.modified_files) if report.modified_files else 'Aucun'}")
                if report.commit_hash:
                    lines.append(f"- **Commit Git créé :** `{report.commit_hash}`")
            lines.extend(["", "## Historique des étapes :"])
            for step in report.history:
                lines.append(f"- **{step.step_name}** ({step.model}, {step.duration_seconds:.2f}s)")
            report_file.write_text("\n".join(lines), encoding="utf-8")
            logger.info(f"Rapport d'audit sauvegardé dans : {report_file}")

        except Exception as e:
            logger.error(f"Erreur lors de la persistance disque : {e}")
