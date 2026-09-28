#!/usr/bin/env python3
"""
Point d'entrée CLI pour exécuter l'orchestrateur multi-agents Claude & TypeSafe Jev.
"""

import argparse
import logging
import sys
from pathlib import Path

from clients.claude_cli import ClaudeCliClient
from clients.jev_client import JevClient
from config import CLAUDE_BIN_PATH, MAX_RETRIES, MOCK_SERVICES, TYPESAFE_API_KEY
from models import StepRecord
from orchestrator import MultiAgentOrchestrator


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
        "1_GENERATION_SPEC": "📝",
        "DEV": "💻",
        "CHECK_QUALITE": "🔍",
        "FEEDBACK": "⚠️",
        "CHECK_SECU": "🛡️",
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
        "--workspace",
        type=str,
        default="./output",
        help="Répertoire où persister le code et la documentation générés",
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
    print(f"• Type de workflow : {report.workflow_type.value if report.workflow_type else 'N/A'}")
    print(f"• Développeur       : {report.dev_specialty.value if report.dev_specialty else 'N/A'}")
    print(f"• Cycles exécutés  : {report.iterations_count}")
    print(f"• Total étapes     : {len(report.history)}")

    print("\n--- CODE PRODUIT ---")
    print(report.code_produit[:600] + ("\n... [tronqué pour affichage]" if len(report.code_produit) > 600 else ""))

    print("\n--- DOCUMENTATION & COMMIT GIT ---")
    print(report.doc_et_commit)

    if args.workspace:
        print(f"\n📂 Fichiers persistés dans le dossier : {Path(args.workspace).resolve()}")

    print("=" * 70 + "\n")


if __name__ == "__main__":
    main()
