"""
Moteur d'orchestration multi-agents Claude & TypeSafe Jev.
Implémente la machine à états finis, le routage déterministe,
l'isolation stricte des contextes et les garde-fous anti-dérive.
"""

import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

from clients.claude_cli import ClaudeCliClient, ClaudeCliError
from clients.jev_client import JevClient
from config import (
    MAX_RETRIES,
    MODEL_HAIKU,
    MODEL_OPUS,
    MODEL_SONNET,
)
from models import (
    DevSpecialty,
    StepRecord,
    WorkflowExecutionReport,
    WorkflowType,
)

logger = logging.getLogger("orchestrator")


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
        max_retries: int = MAX_RETRIES,
        workspace_dir: Optional[str] = None,
        on_step_callback: Optional[Callable[[StepRecord], None]] = None,
    ):
        self.claude = claude_client or ClaudeCliClient()
        self.jev = jev_client or JevClient()
        self.max_retries = max_retries
        self.workspace_dir = workspace_dir
        self.on_step_callback = on_step_callback

    def run(self, prompt_simple: str) -> WorkflowExecutionReport:
        """
        Exécute le workflow multi-agents de bout en bout à partir d'un prompt utilisateur simple.
        """
        report = WorkflowExecutionReport(prompt_simple=prompt_simple)
        logger.info(f"Démarrage du workflow pour : '{prompt_simple[:60]}...'")

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

        # Configuration de la matrice de modèles selon la branche
        is_complexe = (report.workflow_type == WorkflowType.COMPLEXE)
        is_simple = (report.workflow_type == WorkflowType.SIMPLE)

        dev_model = MODEL_OPUS if is_complexe else MODEL_SONNET
        quality_model = MODEL_OPUS if is_complexe else MODEL_SONNET
        feedback_bug_model = MODEL_SONNET if is_complexe else MODEL_HAIKU
        security_model = MODEL_SONNET
        feedback_secu_model = MODEL_SONNET if is_complexe else MODEL_HAIKU
        doc_commit_model = MODEL_SONNET if is_simple else MODEL_HAIKU

        # =========================================================================
        # MACHINE À ÉTATS : DEV <-> QUALITÉ (<-> SÉCURITÉ)
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
                    # Garde-fou contexte : injection ciblée sans historique superflu
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

                # Reset du feedback consommé
                dernier_feedback = ""
                # Transition systématique vers le check qualité
                etape = "CHECK_QUALITE"

            # ---------------------------------------------------------------------
            # Nœud : CHECK BUG / QUALITÉ
            # Contrainte stricte : Ne reçoit QUE le résultat produit par le dev
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

                # Décision binaire par JEV
                logger.info("Validation Qualité / Bug soumise à Jev (noul/binary)...")
                decision_qualite = self.jev.validate(
                    context=f"Code :\n{code_produit}\n\nReview Qualité :\n{review_qualite}",
                    criteria="Le code est-il exempt de bugs, robuste et conforme aux critères de qualité logicielle ?",
                )

                if not decision_qualite:
                    logger.warning(">>> Jev : REJET Qualité/Bug détecté.")
                    # Génération du prompt de retour (Haiku ou Sonnet)
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
                    # Boucle de retour vers le DEV
                    etape = "DEV"
                else:
                    logger.info(">>> Jev : VALIDATION Qualité/Bug accordée.")
                    if is_simple:
                        # Tâche simple : pas de check sécurité -> Direct Doc & Commit
                        etape = "DOC_ET_COMMIT"
                    else:
                        # Tâche moyenne ou complexe -> Check sécurité requis
                        etape = "CHECK_SECURITE"

            # ---------------------------------------------------------------------
            # Nœud : CHECK SÉCURITÉ (Branches Moyenne & Complexe uniquement)
            # Contrainte stricte : Reçoit le résultat ET la review qualité
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

                # Décision binaire sécurité par JEV
                logger.info("Validation Sécurité soumise à Jev (noul/binary)...")
                decision_secu = self.jev.validate(
                    context=f"Code :\n{code_produit}\n\nReview Sécurité :\n{review_securite}",
                    criteria="Le code est-il sécurisé et exempt de toute vulnérabilité de sécurité ?",
                )

                if not decision_secu:
                    logger.warning(">>> Jev : REJET Sécurité détecté.")
                    # Génération du prompt de retour sécurité (Haiku ou Sonnet)
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
                    # Retour au dev -> Repassera OBLIGATOIREMENT par CHECK_QUALITE
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

            # 1. Sauvegarde du code produit
            ext_map = {
                DevSpecialty.PYTHON: ".py",
                DevSpecialty.CSHARP: ".cs",
                DevSpecialty.NODEJS: ".ts",
                DevSpecialty.UI: ".tsx",
            }
            ext = ext_map.get(report.dev_specialty, ".txt")
            code_file = ws_path / f"generated_solution{ext}"
            code_file.write_text(report.code_produit, encoding="utf-8")
            logger.info(f"Code produit sauvegardé dans : {code_file}")

            # 2. Sauvegarde de la documentation
            doc_file = ws_path / "GENERATED_DOC.md"
            doc_file.write_text(report.doc_et_commit, encoding="utf-8")
            logger.info(f"Documentation sauvegardée dans : {doc_file}")

            # 3. Sauvegarde du rapport d'audit
            report_file = ws_path / "WORKFLOW_AUDIT.md"
            lines = [
                f"# Rapport d'Exécution Multi-Agents",
                f"- **Prompt Initial :** {report.prompt_simple}",
                f"- **Type de Workflow :** {report.workflow_type.value if report.workflow_type else 'N/A'}",
                f"- **Spécialité :** {report.dev_specialty.value if report.dev_specialty else 'N/A'}",
                f"- **Itérations :** {report.iterations_count}",
                f"- **Étapes exécutées :** {len(report.history)}",
                "",
                "## Historique des étapes :",
            ]
            for step in report.history:
                lines.append(f"- **{step.step_name}** ({step.model}, {step.duration_seconds:.2f}s)")
            report_file.write_text("\n".join(lines), encoding="utf-8")
            logger.info(f"Rapport d'audit sauvegardé dans : {report_file}")

        except Exception as e:
            logger.error(f"Erreur lors de la persistance disque : {e}")
