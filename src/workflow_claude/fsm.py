"""
Machine à états unique du workflow multi-agents (modes In-Repo et Standalone).

    SPEC ─▶ routage Jev (complexité, spécialité) ─▶ boucle :
        DEV ─▶ [tests] ─▶ QUALITÉ ─▶ [SÉCURITÉ si non simple] ─▶ DOC & COMMIT
         ▲        │ régression        │ rejet Jev          │ rejet Jev
         └────────┴───────────────────┴────────────────────┘   (feedback correctif)

Le moteur (`WorkflowEngine`) ne connaît QUE cette logique. Ce qui diffère selon le mode est porté par un
backend : `InRepoBackend` (édition in-situ d'un dépôt Git, tests, rollback) et `StandaloneBackend`
(génération de code en texte, sans dépôt). Les noms d'étapes (`DEV_IN_SITU_CYCLE_n`, `CHECK_QUALITE_*`...)
sont un contrat implicite utilisé par les tests et par le CLI.
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from . import prompts
from .clients.test_runner import TestResult, is_regression
from .models import DevSpecialty, WorkflowExecutionReport, WorkflowType
from .policy import SPEC_MODEL, ModelPolicy
from .roles import Role
from .ui.terminal import Spinner

if TYPE_CHECKING:  # pragma: no cover
    from .orchestrator import MultiAgentOrchestrator

logger = logging.getLogger("orchestrator")


class WorkflowMaxRetriesExceeded(RuntimeError):
    """Exception levée lorsque le nombre maximum de boucles de feedback est atteint."""
    pass


@dataclass
class LoopState:
    """État de la boucle : ce que voient les relecteurs (diff ou code) et le dernier retour correctif."""
    iter_count: int = 0
    artifact: str = ""          # git diff (In-Repo) ou code produit (Standalone)
    feedback: str = ""
    review_quality: str = ""
    review_security: str = ""


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------
class Backend(ABC):
    """Opérations dépendantes du mode, appelées par le moteur."""

    in_repo: bool = False
    text_only: bool = False     # vrai : aucun outil, répertoire vide (mode Standalone)
    diff_label = "Git Diff"
    complexity_question = ""
    specialty_question = ""
    quality_criteria = ""
    security_criteria = ""
    spec_step = ""

    def __init__(self, orch: "MultiAgentOrchestrator"):
        self.o = orch

    # -- cycle de vie
    def prepare(self, report: WorkflowExecutionReport) -> None:
        """Avant la spécification (baseline des tests en In-Repo)."""

    @abstractmethod
    def spec_prompt(self, request: str) -> str: ...

    @abstractmethod
    def spec_spinner(self) -> str: ...

    def spec_cwd(self) -> Optional[str]:
        return None

    @abstractmethod
    def run_dev(self, report: WorkflowExecutionReport, state: LoopState, models: ModelPolicy) -> None:
        """Appelle l'agent de développement et met à jour `state.artifact`."""

    def run_checks(self, report: WorkflowExecutionReport, state: LoopState) -> Optional[str]:
        """Tests du projet : retourne un feedback correctif en cas de régression, sinon None."""
        return None

    def on_reject(self) -> None:
        """Appelé quand Jev rejette le travail (In-Repo : rollback Git)."""

    # -- noms d'étapes et prompts
    @abstractmethod
    def dev_step(self, n: int) -> str: ...

    @abstractmethod
    def quality_step(self, n: int) -> str: ...

    @abstractmethod
    def security_step(self, n: int) -> str: ...

    @abstractmethod
    def quality_prompt(self, artifact: str) -> str: ...

    @abstractmethod
    def security_prompt(self, artifact: str, quality_review: str) -> str: ...

    @abstractmethod
    def doc_prompt(self, artifact: str) -> str: ...


class InRepoBackend(Backend):
    in_repo = True
    text_only = False
    diff_label = "Git Diff"
    spec_step = "1_EXPLORATION_ET_SPEC"
    complexity_question = "Quel est le niveau de complexité de cette tâche de refactoring / modification ?"
    specialty_question = "Quelle est la spécialité technique du développeur requise pour ce projet ?"
    quality_criteria = "Ce diff git est-il exempt de bugs, robuste et conforme aux critères de qualité logicielle ?"
    security_criteria = "Le code modifié dans ce git diff est-il sécurisé et exempt de toute vulnérabilité de sécurité ?"

    def __init__(self, orch: "MultiAgentOrchestrator"):
        super().__init__(orch)
        self.baseline: Optional[TestResult] = None
        self.spec = ""

    def prepare(self, report: WorkflowExecutionReport) -> None:
        """BASELINE DES TESTS (CYCLE 0) : état de santé initial du projet."""
        o = self.o
        if not (o.run_tests and o.test_runner):
            return
        with Spinner("Vérification de la santé initiale des tests (Baseline)..."):
            baseline = o._run_tests_clean()
        if baseline is None:
            return
        self.baseline = baseline
        report.baseline_tests_passed = baseline.passed
        report.baseline_tests_failed = baseline.failed_tests
        if not baseline.passed:
            known = f" ({len(baseline.failed_tests)} test(s) identifié(s))" if baseline.failed_tests else ""
            logger.warning(
                f"⚠️ BASELINE : La suite de tests échoue DÉJÀ avant toute modification ({baseline.command}){known}. "
                "Les échecs préexistants ne seront pas considérés comme des régressions de Claude."
            )

    def spec_prompt(self, request: str) -> str:
        return prompts.spec_in_repo(request)

    def spec_spinner(self) -> str:
        return "Exploration du codebase et rédaction de la spécification in-situ..."

    def spec_cwd(self) -> Optional[str]:
        return str(self.o.project_dir)

    def run_dev(self, report: WorkflowExecutionReport, state: LoopState, models: ModelPolicy) -> None:
        o = self.o
        self.spec = report.spec_complexe
        prompt = prompts.dev_in_repo(report.dev_specialty, self.spec, state.feedback)
        t0 = time.perf_counter()
        with Spinner(f"Développement chirurgical in-situ ({models.dev})..."):
            o._call_claude(
                Role.DEV, prompt, models.dev, cwd=str(o.project_dir),
                append_system_prompt=prompts.specialty_system_prompt(report.dev_specialty),
            )
        dur = time.perf_counter() - t0

        # Capture du diff réel dans le dépôt Git (y compris nouveaux fichiers untracked via intent-to-add)
        diff, excluded = o.git.get_diff_split()
        modified_files = o.git.get_modified_files()
        report.git_diff = diff
        report.modified_files = modified_files
        report.code_produit = diff
        state.artifact = diff + o.excluded_files_note(excluded)

        o._record_step(
            report, self.dev_step(state.iter_count), models.dev, prompt,
            f"Fichiers modifiés: {', '.join(modified_files)}\n\nGit Diff:\n{diff}", dur,
        )

    def run_checks(self, report: WorkflowExecutionReport, state: LoopState) -> Optional[str]:
        """BOUCLE DE TESTS AUTOMATISÉE (oracle de vérité)."""
        o = self.o
        if not (o.run_tests and o.test_runner):
            return None
        with Spinner("Exécution de la suite de tests du projet..."):
            result = o._run_tests_clean()
        if result is None:
            return None

        report.tests_passed = result.passed
        report.tests_output = result.output
        report.tests_failed = result.failed_tests
        n = state.iter_count
        # Régression = un test échoue qui n'échouait pas dans la baseline (comparaison d'ensembles, pas de texte)
        if is_regression(self.baseline, result):
            logger.warning(f">>> Échec des tests du projet ({result.command}). Déclenchement de l'auto-correction...")
            o._record_step(report, f"TESTS_FAILED_CYCLE_{n}", "test_runner", result.command, result.output, result.duration_seconds)
            return prompts.tests_failed_feedback(result.command, result.output)

        logger.info(f">>> Succès de la suite de tests ({result.command}) en {result.duration_seconds:.2f}s.")
        o._record_step(
            report, f"TESTS_PASSED_CYCLE_{n}", "test_runner", result.command,
            f"Tests passés avec succès :\n{result.output[:300]}", result.duration_seconds,
        )
        return None

    def on_reject(self) -> None:
        self.o.git.rollback()

    def dev_step(self, n: int) -> str:
        return f"DEV_IN_SITU_CYCLE_{n}"

    def quality_step(self, n: int) -> str:
        return f"CHECK_QUALITE_DIFF_CYCLE_{n}"

    def security_step(self, n: int) -> str:
        return f"CHECK_SECU_DIFF_CYCLE_{n}"

    def quality_prompt(self, artifact: str) -> str:
        return prompts.quality_in_repo(artifact, self.o.max_prompt_chars)

    def security_prompt(self, artifact: str, quality_review: str) -> str:
        return prompts.security_in_repo(artifact, quality_review, self.o.max_prompt_chars)

    def doc_prompt(self, artifact: str) -> str:
        return prompts.doc_in_repo(artifact, self.o.max_prompt_chars)


class StandaloneBackend(Backend):
    in_repo = False
    text_only = True
    diff_label = "Code"
    spec_step = "1_GENERATION_SPEC"
    complexity_question = "Quel est le niveau de complexité de cette tâche ?"
    specialty_question = "Quelle est la spécialité technique du développeur requise ?"
    quality_criteria = "Le code est-il exempt de bugs, robuste et conforme aux critères de qualité logicielle ?"
    security_criteria = "Le code est-il sécurisé et exempt de toute vulnérabilité de sécurité ?"

    def spec_prompt(self, request: str) -> str:
        return prompts.spec_standalone(request)

    def spec_spinner(self) -> str:
        return "Génération de la spécification technique..."

    def run_dev(self, report: WorkflowExecutionReport, state: LoopState, models: ModelPolicy) -> None:
        o = self.o
        prompt = prompts.dev_standalone(report.dev_specialty, report.spec_complexe, state.artifact, state.feedback)
        t0 = time.perf_counter()
        with Spinner(f"Développement ({models.dev})..."):
            code = o._call_claude(
                Role.DEV, prompt, models.dev, standalone=True,
                append_system_prompt=prompts.specialty_system_prompt(report.dev_specialty),
            )
        dur = time.perf_counter() - t0
        report.code_produit = code
        state.artifact = code
        o._record_step(report, self.dev_step(state.iter_count), models.dev, prompt, code, dur)

    def dev_step(self, n: int) -> str:
        return f"DEV_CYCLE_{n}"

    def quality_step(self, n: int) -> str:
        return f"CHECK_QUALITE_CYCLE_{n}"

    def security_step(self, n: int) -> str:
        return f"CHECK_SECU_CYCLE_{n}"

    def quality_prompt(self, artifact: str) -> str:
        return prompts.quality_standalone(artifact, self.o.max_prompt_chars)

    def security_prompt(self, artifact: str, quality_review: str) -> str:
        return prompts.security_standalone(artifact, quality_review, self.o.max_prompt_chars)

    def doc_prompt(self, artifact: str) -> str:
        return prompts.doc_standalone(artifact, self.o.max_prompt_chars)


# ---------------------------------------------------------------------------
# Moteur
# ---------------------------------------------------------------------------
class WorkflowEngine:
    """Exécute la machine à états sur un backend. `run()` retourne True si le workflow est validé."""

    def __init__(
        self,
        orch: "MultiAgentOrchestrator",
        backend: Backend,
        report: WorkflowExecutionReport,
        raise_on_failure: bool = False,
    ):
        self.o = orch
        self.b = backend
        self.report = report
        self.raise_on_failure = raise_on_failure
        self.models: Optional[ModelPolicy] = None

    def run(self) -> bool:
        report, b = self.report, self.b
        b.prepare(report)
        self._spec()
        self._route()
        self.models = ModelPolicy.for_type(report.workflow_type)

        state = LoopState()
        etape = "DEV"
        while etape != "DOC_ET_COMMIT":
            # Le circuit breaker n'est évalué qu'à l'entrée d'un nouveau cycle DEV : le dernier
            # cycle autorisé va jusqu'au bout de sa revue (tests, qualité, sécurité).
            if etape == "DEV" and state.iter_count >= self.o.max_retries:
                return self._circuit_breaker()

            if etape == "DEV":
                etape = self._dev(state)
            elif etape == "CHECK_QUALITE":
                etape = self._quality(state)
            elif etape == "CHECK_SECURITE":
                etape = self._security(state)

        self._doc(state)
        logger.info(f"Workflow validé en {state.iter_count} itération(s).")
        return True

    # -- étapes
    def _spec(self) -> None:
        o, b, report = self.o, self.b, self.report
        logger.info(f">>> Étape 1 : Spécification technique ({SPEC_MODEL})")
        prompt = b.spec_prompt(report.prompt_simple)
        t0 = time.perf_counter()
        with Spinner(b.spec_spinner()):
            spec = o._call_claude(Role.SPEC, prompt, SPEC_MODEL, cwd=b.spec_cwd(), standalone=b.text_only)
        report.spec_complexe = spec
        o._record_step(report, b.spec_step, SPEC_MODEL, prompt, spec, time.perf_counter() - t0)

    def _route(self) -> None:
        o, b, report = self.o, self.b, self.report
        logger.info(">>> Étape 2 : Aiguillage de complexité et spécialité dev via Jev")
        workflow_type = o._jev_classify(
            report, "ROUTAGE_COMPLEXITE", report.spec_complexe,
            [WorkflowType.SIMPLE.value, WorkflowType.MOYENNE.value, WorkflowType.COMPLEXE.value],
            b.complexity_question, prompts.COMPLEXITY_DESCRIPTIONS,
        )
        report.workflow_type = WorkflowType.from_str(workflow_type)

        specialty = o._jev_classify(
            report, "ROUTAGE_SPECIALITE", report.spec_complexe,
            [DevSpecialty.CSHARP.value, DevSpecialty.NODEJS.value, DevSpecialty.UI.value, DevSpecialty.PYTHON.value],
            b.specialty_question, prompts.SPECIALTY_DESCRIPTIONS,
        )
        report.dev_specialty = DevSpecialty.from_str(specialty)
        logger.info(f"Aiguillage Jev validé : Workflow={report.workflow_type.value}, Dev={report.dev_specialty.value}")

    def _circuit_breaker(self) -> bool:
        report = self.report
        err = (
            f"Circuit breaker : Nombre maximum d'itérations ({self.o.max_retries}) atteint. "
            "Arrêt forcé avant un nouveau cycle de développement pour prévenir une consommation incontrôlée."
        )
        if report.tests_passed is False:
            failing = f" ({', '.join(report.tests_failed[:5])})" if report.tests_failed else ""
            err += f" Les tests du projet échouaient encore au dernier cycle{failing}."
        logger.error(err)
        report.error_message = err
        report.is_success = False
        # Pas de nettoyage ici : en mode In-Repo, sortir sans guard.leave() annule le travail, revient sur la
        # branche d'origine et restaure le stash de l'utilisateur (voir isolation.py).
        if self.raise_on_failure:
            raise WorkflowMaxRetriesExceeded(err)
        return False

    def _dev(self, state: LoopState) -> str:
        report, b, models = self.report, self.b, self.models
        state.iter_count += 1
        report.iterations_count = state.iter_count
        logger.info(f"--- Cycle {state.iter_count}/{self.o.max_retries} : DÉVELOPPEMENT (Modèle: {models.dev}) ---")

        b.run_dev(report, state, models)
        state.feedback = ""

        test_feedback = b.run_checks(report, state)
        if test_feedback:
            state.feedback = test_feedback
            return "DEV"
        return "CHECK_QUALITE"

    def _review(self, step: str, role: Role, prompt: str, model: str, spinner: str) -> str:
        o, report = self.o, self.report
        t0 = time.perf_counter()
        with Spinner(spinner):
            review = o._call_claude(role, prompt, model)
        o._record_step(report, step, model, prompt, review, time.perf_counter() - t0)
        return review

    def _feedback(self, step: str, prompt: str, model: str) -> str:
        o, report = self.o, self.report
        t0 = time.perf_counter()
        feedback = o._call_claude(Role.FEEDBACK, prompt, model)
        o._record_step(report, step, model, prompt, feedback, time.perf_counter() - t0)
        return feedback

    def _quality(self, state: LoopState) -> str:
        report, b, models, o = self.report, self.b, self.models, self.o
        n = state.iter_count
        logger.info(f"--- Cycle {n} : CHECK QUALITÉ (Modèle: {models.quality}) ---")
        state.review_quality = self._review(
            b.quality_step(n), Role.QUALITY, b.quality_prompt(state.artifact), models.quality,
            f"Audit Qualité ({models.quality})...",
        )
        report.review_qualite = state.review_quality

        logger.info("Validation Qualité soumise à Jev (noul)...")
        valid = o._jev_validate(
            report, f"VALIDATION_QUALITE_CYCLE_{n}", state.artifact, state.review_quality, "Review Qualité",
            b.quality_criteria, diff_label=b.diff_label,
        )
        if not valid:
            logger.warning(">>> Jev : REJET Qualité/Bug détecté. Retour au développement...")
            b.on_reject()
            state.feedback = self._feedback(
                f"FEEDBACK_QUALITE_CYCLE_{n}", prompts.feedback_quality(state.review_quality), models.feedback_quality
            )
            return "DEV"
        logger.info(">>> Jev : VALIDATION Qualité accordée.")
        return "CHECK_SECURITE" if models.run_security else "DOC_ET_COMMIT"

    def _security(self, state: LoopState) -> str:
        report, b, models, o = self.report, self.b, self.models, self.o
        n = state.iter_count
        logger.info(f"--- Cycle {n} : CHECK SÉCURITÉ (Modèle: {models.security}) ---")
        state.review_security = self._review(
            b.security_step(n), Role.SECURITY, b.security_prompt(state.artifact, state.review_quality), models.security,
            f"Audit Cyber-Sécurité ({models.security})...",
        )
        report.review_securite = state.review_security

        logger.info("Validation Sécurité soumise à Jev (noul)...")
        valid = o._jev_validate(
            report, f"VALIDATION_SECU_CYCLE_{n}", state.artifact, state.review_security, "Review Sécurité",
            b.security_criteria, diff_label=b.diff_label,
        )
        if not valid:
            logger.warning(">>> Jev : REJET Sécurité détecté. Retour au développement...")
            b.on_reject()
            state.feedback = self._feedback(
                f"FEEDBACK_SECU_CYCLE_{n}", prompts.feedback_security(state.review_security), models.feedback_security
            )
            return "DEV"
        logger.info(">>> Jev : VALIDATION Sécurité accordée.")
        return "DOC_ET_COMMIT"

    def _doc(self, state: LoopState) -> None:
        o, b, report, models = self.o, self.b, self.report, self.models
        logger.info(f">>> Étape Finale : documentation et message de commit (Modèle: {models.doc})")
        prompt = b.doc_prompt(state.artifact)
        t0 = time.perf_counter()
        with Spinner(f"Génération de la documentation et du commit ({models.doc})..."):
            doc = o._call_claude(Role.DOC, prompt, models.doc)
        report.doc_et_commit = doc
        o._record_step(report, "FINAL_DOC_ET_COMMIT", models.doc, prompt, doc, time.perf_counter() - t0)
