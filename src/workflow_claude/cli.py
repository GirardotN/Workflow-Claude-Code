#!/usr/bin/env python3
"""
Point d'entrée CLI pour exécuter l'orchestrateur multi-agents Claude & TypeSafe Jev.
"""

import argparse
import logging
import sys
from pathlib import Path

from .clients.claude_cli import ClaudeCliClient, ClaudeCliError, detected_billing_env
from .clients.jev_client import JevApiError, JevClient
from .config import (
    ALLOW_API_KEY,
    CLAUDE_BIN_PATH,
    DEFAULT_ALLOW_BASH,
    DEFAULT_RUN_TESTS,
    DEFAULT_USE_BRANCH,
    JEV_SEND,
    MAX_RETRIES,
    MOCK_SERVICES,
)
from .doctor import run_doctor
from .isolation import WorkflowPreconditionError
from .models import StepRecord
from .orchestrator import MultiAgentOrchestrator
from .ui.terminal import confirm_action, format_colored_diff

# Codes de sortie (utilisables dans des scripts et en CI)
EXIT_OK = 0
EXIT_ERROR = 1          # erreur inattendue ou précondition non remplie (dépôt vide, identité Git absente...)
EXIT_INCOMPLETE = 2     # workflow terminé sans succès (circuit breaker, commit refusé...)
EXIT_CLAUDE = 3         # session / quota / timeout du CLI Claude
EXIT_JEV = 4            # TypeSafe Jev : clé absente ou refusée, service indisponible, réponse invalide
EXIT_INTERRUPTED = 130  # Ctrl-C

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


def ask_merge(report, assume_yes: bool = False) -> bool:
    """
    Décision de fusion, appelée par l'orchestrateur une fois les modifications validées et
    committées sur la branche d'isolation (avant le retour sur la branche d'origine).
    """
    print("\n" + "-" * 70)
    print(f"🌿 Modifications validées et committées [{report.commit_hash}] sur la branche '{report.branch_name}'.")
    question = f"Voulez-vous fusionner '{report.branch_name}' dans '{report.original_branch}' ?"
    merge = assume_yes or confirm_action(question, default=True)
    print("-" * 70)
    return merge


def main(argv=None) -> int:
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
        help="Autoriser l'outil Bash pour l'agent de développement, restreint à une liste blanche de commandes (tests, lecture) ; git qui modifie l'état et rm/curl/sudo restent interdits (défaut: désactivé)",
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
        "--allow-api-key",
        action="store_true",
        default=ALLOW_API_KEY,
        help="Laisser passer ANTHROPIC_API_KEY & co au CLI Claude (FACTURATION À L'USAGE). "
             "Par défaut ces variables sont retirées pour n'utiliser que l'abonnement.",
    )
    parser.add_argument(
        "--jev-send",
        choices=["full", "review-only"],
        default=JEV_SEND if JEV_SEND in ("full", "review-only") else "full",
        help="Données envoyées à TypeSafe Jev pour les validations : 'full' = diff (secrets masqués, plafonné) + revue ; "
             "'review-only' = revue seule, aucun code transmis",
    )
    parser.add_argument(
        "--doctor",
        action="store_true",
        help="Vérifier l'environnement (git, CLI Claude, session, options, clés) sans consommer de quota, puis quitter",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Activer la journalisation détaillée de débogage",
    )

    args = parser.parse_args(argv)
    setup_logging(args.verbose)

    if args.doctor:
        return run_doctor(ClaudeCliClient(allow_api_key=args.allow_api_key), allow_api_key=args.allow_api_key, mock=args.mock)

    # Échec rapide : sans clé TypeSafe (et sans --mock), on ne démarre pas (les validations ne seraient pas fiables)
    try:
        jev_client = JevClient(mock_mode=args.mock)
    except JevApiError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        return EXIT_JEV

    print("\n" + "=" * 70)
    print("🚀 ORCHESTRATEUR MULTI-AGENTS CLAUDE & TYPESAFE JEV")
    print("=" * 70)
    print(f"• Prompt initial   : {args.prompt}")
    print(f"• Mode simulation  : {'OUI (--mock)' if args.mock else 'NON (Claude CLI + TypeSafe API)'}")
    print(f"• Mode de travail  : {'Standalone forcé (--standalone)' if args.standalone else ('In-Repo: ' + args.project_dir if args.project_dir else 'Auto (In-Repo si dépôt Git)')}")
    if args.commit:
        if args.branch:
            print("• Auto-commit Git  : --commit sans effet avec l'isolation par branche (commit systématique sur workflow/ai-*)")
        else:
            print("• Auto-commit Git  : Activé (--commit)")
    print(f"• Isolation branche: {'Activée (--branch)' if args.branch else 'Désactivée (--no-branch)'}")
    print(f"• Tests auto       : {'Activés (--run-tests)' if args.run_tests else 'Désactivés (--no-tests)'}")
    print(f"• Outil Bash       : {'Autorisé (--allow-bash)' if args.allow_bash else 'Désactivé (mode sandbox sécurisé)'}")
    print(f"• Claude CLI path  : {CLAUDE_BIN_PATH}")
    print(f"• TypeSafe Jev     : {'SIMULATION (--mock)' if args.mock else 'API réelle (clé configurée)'}")
    if not args.mock:
        print(f"• Envoi à Jev      : {'revue seule (aucun code transmis)' if args.jev_send == 'review-only' else 'diff (secrets masqués, plafonné) + revue'}")
    print(f"• Max Retries      : {args.max_retries}")
    print(f"• Workspace        : {args.workspace}")
    print("=" * 70 + "\n")
    if args.mock:
        print("⚠️  MODE SIMULATION (--mock) : Claude et Jev sont simulés. Les validations NE SONT PAS FIABLES : "
              "ne l'utilisez pas pour valider du vrai code.\n")

    billing_vars = detected_billing_env()
    if billing_vars and not args.mock:
        if args.allow_api_key:
            print(f"⚠️ {', '.join(billing_vars)} détecté(s) et --allow-api-key actif : les appels Claude peuvent être FACTURÉS À L'USAGE.\n")
        else:
            print(f"ℹ️ {', '.join(billing_vars)} détecté(s) dans l'environnement : retiré(s) du sous-processus Claude "
                  "(abonnement uniquement). --allow-api-key pour les conserver.\n")

    # --mock : l'agent de développement simulé n'écrit qu'un fichier marqueur, jamais dans le code du projet
    claude_client = ClaudeCliClient(mock_mode=args.mock, allow_api_key=args.allow_api_key, mock_edit_files=False)

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
        on_merge_decision=lambda rep: ask_merge(rep, assume_yes=args.yes),
        jev_send=args.jev_send,
    )

    try:
        report = orchestrator.run(args.prompt)
    except KeyboardInterrupt:
        print("\n⛔ Interrompu (Ctrl-C). Votre dépôt a été remis dans son état d'origine et vos modifications restaurées.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except JevApiError as e:
        print(f"\n❌ TypeSafe Jev : {e}\nLe workflow a été arrêté (aucune validation par défaut). Votre dépôt a été remis en état.", file=sys.stderr)
        return EXIT_JEV
    except ClaudeCliError as e:
        print(f"\n❌ Claude CLI : {e}\nVotre dépôt a été remis en état.", file=sys.stderr)
        return EXIT_CLAUDE
    except WorkflowPreconditionError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as e:
        print(f"\n❌ Erreur fatale durant le workflow : {e}", file=sys.stderr)
        return EXIT_ERROR

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
    if report.jev_mode == "mock":
        print("• Jev              : SIMULATION (validations non fiables)")
    if report.decisions:
        print(f"• Décisions Jev    : {len(report.decisions)}")
        for d in report.decisions:
            value = f" ({d['value']:.2f})" if isinstance(d.get("value"), (int, float)) else ""
            mark = {True: "✅", False: "❌"}.get(d["result"], "•") if d["kind"] == "noul" else "•"
            print(f"    {mark} {d['step']}: {d['result']}{value}")

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

        # Bilan de l'isolation Git : l'orchestrateur est déjà revenu sur la branche d'origine
        # et a restauré le stash ; il ne reste qu'à informer l'utilisateur.
        if report.is_success and report.merged:
            print(f"\n✅ Fusion réussie dans '{report.original_branch}' (vous êtes de retour sur cette branche).")
        elif report.is_success and report.branch_name and report.branch_name != report.original_branch:
            print("\n" + "-" * 70)
            print(f"ℹ️ Les modifications validées sont sur la branche '{report.branch_name}' (vous êtes de retour sur '{report.original_branch}').")
            if report.original_branch and report.original_branch != "HEAD":
                print(f"  Pour les intégrer : git merge {report.branch_name}")
            else:
                print(f"  Pour les intégrer : git merge {report.branch_name} (depuis la branche de votre choix)")
            print("-" * 70)
        if report.stash_restored is False:
            print(
                "\n⚠️ Vos modifications locales mises en réserve n'ont pas pu être restaurées automatiquement "
                "(conflit). Elles sont conservées : git stash list, puis git stash pop."
            )

    else:
        print("\n--- CODE PRODUIT ---")
        print(report.code_produit[:600] + ("\n... [tronqué pour affichage]" if len(report.code_produit) > 600 else ""))

    print("\n--- DOCUMENTATION & COMMIT GIT ---")
    print(report.doc_et_commit)

    if args.workspace:
        print(f"\n📂 Fichiers persistés dans le dossier : {Path(args.workspace).resolve()}")

    print("=" * 70 + "\n")
    return EXIT_OK if report.is_success else EXIT_INCOMPLETE


if __name__ == "__main__":
    sys.exit(main())

