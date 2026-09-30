#!/usr/bin/env python3
"""
Point d'entrée CLI pour exécuter l'orchestrateur multi-agents Claude & TypeSafe Jev.
"""

import argparse
import logging
import sys
from pathlib import Path

from .clients.claude_cli import ClaudeCliClient
from .clients.git_client import GitClient
from .clients.jev_client import JevClient
from .config import (
    CLAUDE_BIN_PATH,
    DEFAULT_ALLOW_BASH,
    DEFAULT_RUN_TESTS,
    DEFAULT_USE_BRANCH,
    MAX_RETRIES,
    MOCK_SERVICES,
    TYPESAFE_API_KEY,
)
from .models import StepRecord
from .orchestrator import MultiAgentOrchestrator
from .ui.terminal import confirm_action, format_colored_diff

# Support de l'encodage UTF-8 sous Windows (cmd.exe / PowerShell)
if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def setup_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    format_str = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(level=level, format=format_str, datefmt="%H:%M:%S")


def print_step(step: StepRecord):
    icons = {
        "1_EXPLORATION": "🔍",
        "1_GENERATION_SPEC": "📝",
        "DEV": "💻",
        "TESTS_PASSED": "🧪✅",
        "TESTS_FAILED": "🧪❌",
        "CHECK_QUALITE": "🛡️",
        "FEEDBACK": "⚠️",
        "CHECK_SECU": "🔒",
        "FINAL_DOC": "📦",
    }
    icon = "⚙️"
    for k, v in icons.items():
        if k in step.step_name:
            icon = v
            break
    print(f"  {icon} [{step.step_name}] Modèle: {step.model} ({step.duration_seconds:.2f}s)")


def main():
    parser = argparse.ArgumentParser(
        description="Orchestrateur Multi-Agents Claude & TypeSafe Jev (Zero crédit Claude API)"
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        default="Créer un microservice FastAPI d'authentification JWT avec rate limiting",
        help="Prompt simple décrivant la tâche de développement",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        default=MOCK_SERVICES,
        help="Exécuter en mode simulation/mock (sans appel réseau/CLI)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=MAX_RETRIES,
        help=f"Nombre maximum d'allers-retours de feedback (défaut: {MAX_RETRIES})",
    )
    parser.add_argument(
        "--project-dir",
        type=str,
        default=None,
        help="Répertoire du projet existant à modifier (active le mode In-Repo si sous Git)",
    )
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Forcer le mode autonome (génération d'un fichier dans output/ au lieu d'éditer le projet)",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Créer automatiquement le commit Git conventionnel si les modifications sont validées",
    )
    parser.add_argument(
        "--branch",
        action="store_true",
        default=DEFAULT_USE_BRANCH,
        help="Isoler le travail sur une branche dédiée workflow/ai-* (défaut: activé)",
    )
    parser.add_argument(
        "--no-branch",
        action="store_false",
        dest="branch",
        help="Travailler directement sur la branche active sans créer de branche d'isolation",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        default=False,
        help="Fusionner automatiquement la branche d'isolation dans la branche principale en fin de succès",
    )
    parser.add_argument(
        "--allow-bash",
        action="store_true",
        default=DEFAULT_ALLOW_BASH,
        help="Autoriser Claude à exécuter l'outil Bash (défaut: désactivé pour sécurité)",
    )
    parser.add_argument(
        "--run-tests",
        action="store_true",
        default=DEFAULT_RUN_TESTS,
        help="Exécuter automatiquement la suite de tests du projet hôte (pytest, npm test, etc.)",
    )
    parser.add_argument(
        "--no-tests",
        action="store_false",
        dest="run_tests",
        help="Ne pas exécuter les tests du projet",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        default=False,
        help="Accepter automatiquement les confirmations sans invite interactive",
    )
    parser.add_argument(
        "--workspace",
        type=str,
        default="./output",
        help="Répertoire où persister le rapport d'audit et la documentation",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Activer la journalisation détaillée de débogage",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    print("\n" + "=" * 70)
    print("🚀 ORCHESTRATEUR MULTI-AGENTS CLAUDE & TYPESAFE JEV")
    print("=" * 70)
    print(f"• Prompt initial   : {args.prompt}")
    print(f"• Mode simulation  : {'OUI (--mock)' if args.mock else 'NON (Claude CLI + TypeSafe API)'}")
    print(f"• Mode de travail  : {'Standalone forcé (--standalone)' if args.standalone else ('In-Repo: ' + args.project_dir if args.project_dir else 'Auto (In-Repo si dépôt Git)')}")
    if args.commit:
        print("• Auto-commit Git  : Activé (--commit)")
    print(f"• Isolation branche: {'Activée (--branch)' if args.branch else 'Désactivée (--no-branch)'}")
    print(f"• Tests auto       : {'Activés (--run-tests)' if args.run_tests else 'Désactivés (--no-tests)'}")
    print(f"• Outil Bash       : {'Autorisé (--allow-bash)' if args.allow_bash else 'Désactivé (mode sandbox sécurisé)'}")
    print(f"• Claude CLI path  : {CLAUDE_BIN_PATH}")
    print(f"• TypeSafe API Key : {'Définie' if TYPESAFE_API_KEY else 'Non configurée (fallback mock auto)'}")
    print(f"• Max Retries      : {args.max_retries}")
    print(f"• Workspace        : {args.workspace}")
    print("=" * 70 + "\n")

    claude_client = ClaudeCliClient(mock_mode=args.mock)
    jev_client = JevClient(mock_mode=args.mock)

    orchestrator = MultiAgentOrchestrator(
        claude_client=claude_client,
        jev_client=jev_client,
        max_retries=args.max_retries,
        workspace_dir=args.workspace,
        project_dir=args.project_dir or ".",
        standalone_mode=args.standalone,
        auto_commit=args.commit,
        use_branch=args.branch,
        auto_merge=args.merge,
        allow_bash=args.allow_bash,
        run_tests=args.run_tests,
        on_step_callback=print_step,
    )

    try:
        report = orchestrator.run(args.prompt)
    except Exception as e:
        print(f"\n❌ Erreur fatale durant le workflow : {e}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 70)
    if report.is_success:
        print("✅ WORKFLOW TERMINÉ AVEC SUCCÈS")
    else:
        print(f"⚠️ WORKFLOW INCOMPLET : {report.error_message}")
    print("=" * 70)
    print(f"• Mode effectif    : {'In-Repo (Modifications directes dans le projet)' if report.is_in_repo else 'Standalone (Fichier unique dans output/)'}")
    print(f"• Type de workflow : {report.workflow_type.value if report.workflow_type else 'N/A'}")
    print(f"• Développeur       : {report.dev_specialty.value if report.dev_specialty else 'N/A'}")
    print(f"• Cycles exécutés  : {report.iterations_count}")
    print(f"• Total étapes     : {len(report.history)}")

    if report.is_in_repo:
        print(f"• Dépôt cible      : {report.project_dir}")
        if report.branch_name:
            print(f"• Branche de travail: {report.branch_name}")
        if report.original_branch:
            print(f"• Branche source   : {report.original_branch}")
        if report.tests_passed is not None:
            print(f"• Suite de tests   : {'✅ Succès (Oracle vert)' if report.tests_passed else '❌ Échecs détectés'}")
        print(f"• Fichiers modifiés: {', '.join(report.modified_files) if report.modified_files else 'Aucun'}")
        if report.commit_hash:
            print(f"• Commit Git créé  : [{report.commit_hash}]")

        if report.git_diff:
            print("\n--- GIT DIFF DES MODIFICATIONS IN-SITU ---")
            diff_display = report.git_diff[:2500] + ("\n... [tronqué pour affichage]" if len(report.git_diff) > 2500 else "")
            print(format_colored_diff(diff_display))

        # Proposition de merge interactif si on est resté sur la branche isolée
        if (
            report.is_success
            and report.branch_name
            and report.original_branch
            and report.branch_name != report.original_branch
            and not args.merge
        ):
            print("\n" + "-" * 70)
            print(f"🌿 Les modifications ont été appliquées et validées sur la branche '{report.branch_name}'.")
            if args.yes or confirm_action(f"Voulez-vous fusionner '{report.branch_name}' dans '{report.original_branch}' ?", default=True):
                git_c = GitClient(report.project_dir)
                git_c.checkout_branch(report.original_branch)
                if git_c.merge_branch(report.branch_name):
                    git_c.delete_branch(report.branch_name)
                    print(f"✅ Fusion réussie ! Vous êtes de retour sur '{report.original_branch}'.")
                else:
                    print(f"⚠️ Échec du merge automatique. Vous pouvez inspecter la branche : git checkout {report.branch_name}")
            else:
                print(f"ℹ️ Branche '{report.branch_name}' conservée. Pour la fusionner manuellement :\n  git checkout {report.original_branch}\n  git merge {report.branch_name}")
                if confirm_action(f"Souhaitez-vous revenir sur votre branche d'origine '{report.original_branch}' ?", default=True):
                    git_c = GitClient(report.project_dir)
                    git_c.checkout_branch(report.original_branch)
                    print(f"✅ Vous êtes de retour sur '{report.original_branch}'.")
            print("-" * 70)

    else:
        print("\n--- CODE PRODUIT ---")
        print(report.code_produit[:600] + ("\n... [tronqué pour affichage]" if len(report.code_produit) > 600 else ""))

    print("\n--- DOCUMENTATION & COMMIT GIT ---")
    print(report.doc_et_commit)

    if args.workspace:
        print(f"\n📂 Fichiers persistés dans le dossier : {Path(args.workspace).resolve()}")

    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()

