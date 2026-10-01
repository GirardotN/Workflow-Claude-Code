#!/usr/bin/env python3
"""
Point d'entrée CLI pour exécuter l'orchestrateur multi-agents Claude & TypeSafe Jev.
"""

import argparse
import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Optional, Tuple

from . import __version__
from .clients.claude_cli import ClaudeCliClient, ClaudeCliError, detected_billing_env
from .clients.git_client import GitClient
from .clients.jev_client import JevApiError, JevClient
from .clients.test_runner import TestRunner
from .config import (
    ALLOW_API_KEY,
    CLAUDE_BIN_PATH,
    DEFAULT_ALLOW_BASH,
    DEFAULT_DOC_EDIT,
    DEFAULT_RUN_TESTS,
    DEFAULT_USE_BRANCH,
    JEV_SEND,
    MAX_RETRIES,
    MOCK_SERVICES,
    TEST_COMMAND,
    TEST_TIMEOUT_SECONDS,
)
from .doctor import run_doctor
from .isolation import WorkflowPreconditionError
from .models import StepRecord, WorkflowExecutionReport
from .orchestrator import MultiAgentOrchestrator
from .ui.terminal import confirm_action, format_colored_diff, set_color_enabled

# Codes de sortie (utilisables dans des scripts et en CI)
EXIT_OK = 0
EXIT_ERROR = 1          # erreur inattendue ou précondition non remplie (dépôt vide, identité Git absente...)
EXIT_INCOMPLETE = 2     # workflow terminé sans succès (circuit breaker, commit refusé...) ; 2 = aussi erreur d'usage argparse
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


# ---------------------------------------------------------------------------
# Journalisation et affichage
# ---------------------------------------------------------------------------
def setup_logging(verbose: bool, log_file: Optional[str] = None) -> Optional[logging.Handler]:
    """Configure les logs (stderr) ; avec `log_file`, ajoute un fichier UTF-8 et retourne son handler."""
    level = logging.DEBUG if verbose else logging.INFO
    format_str = "%(asctime)s [%(levelname)s] %(message)s"
    logging.basicConfig(level=level, format=format_str, datefmt="%H:%M:%S")
    if not log_file:
        return None
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter(format_str, datefmt="%H:%M:%S"))
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.DEBUG and verbose:
        root.setLevel(logging.DEBUG)
    return handler


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
        "DOC_EDIT": "📚",
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


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="workflow",
        description="Orchestrateur Multi-Agents Claude & TypeSafe Jev (zéro crédit API Claude). "
                    "Exemple : workflow --project-dir . \"Ajoute un tri par date dans l'onglet x\"",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "prompt",
        nargs="?",
        default=None,
        help="Tâche à réaliser, en langage naturel (obligatoire, sauf avec --doctor)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        default=MOCK_SERVICES,
        help="Exécuter en mode simulation/mock (sans appel réseau/CLI) : validations NON fiables, aucun fichier du projet modifié",
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
        help="Répertoire du projet existant à modifier : doit être un dépôt Git (sinon utilisez --standalone). "
             "Sans cette option : le dossier courant s'il est un dépôt Git, sinon mode Standalone",
    )
    parser.add_argument(
        "--standalone",
        action="store_true",
        help="Forcer le mode autonome (génération d'un fichier dans le dossier de rapports au lieu d'éditer le projet)",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Avec --no-branch : créer le commit conventionnel sur la branche active (sans effet avec l'isolation par branche)",
    )
    parser.add_argument(
        "--branch",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_USE_BRANCH,
        help="Isoler le travail sur une branche dédiée workflow/ai-* (défaut: activé ; --no-branch pour travailler sur la branche active)",
    )
    parser.add_argument(
        "--merge",
        action="store_true",
        default=False,
        help="Fusionner automatiquement la branche d'isolation dans la branche d'origine en fin de succès",
    )
    parser.add_argument(
        "--allow-bash",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_ALLOW_BASH,
        help="Autoriser l'outil Bash pour l'agent de développement, restreint à une liste blanche de commandes "
             "(tests, lecture) ; git qui modifie l'état et rm/curl/sudo restent interdits (défaut: désactivé)",
    )
    parser.add_argument(
        "--run-tests",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_RUN_TESTS,
        help="Exécuter la suite de tests du projet hôte comme oracle (défaut: activé)",
    )
    parser.add_argument(
        "--no-tests",
        action="store_false",
        dest="run_tests",
        help="Ne pas exécuter les tests du projet (équivalent de --no-run-tests)",
    )
    parser.add_argument(
        "--test-cmd",
        type=str,
        default=None,
        metavar="COMMANDE",
        help="Commande de test du projet (remplace la détection automatique ; sinon TEST_COMMAND ou .workflow.toml)",
    )
    parser.add_argument(
        "--test-timeout",
        type=int,
        default=TEST_TIMEOUT_SECONDS,
        metavar="SECONDES",
        help=f"Délai maximum de la suite de tests, l'arbre de processus est tué au-delà (défaut: {TEST_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        default=False,
        help="Accepter automatiquement les confirmations sans invite interactive (fusion)",
    )
    parser.add_argument(
        "--workspace",
        type=str,
        default="./output",
        help="Répertoire où persister rapport d'audit, report.json, patch et documentation (écrits aussi en cas d'échec ; "
             "exclu localement de git s'il est dans le dépôt cible)",
    )
    parser.add_argument(
        "--allow-api-key",
        action="store_true",
        default=ALLOW_API_KEY,
        help="Laisser passer ANTHROPIC_API_KEY & co au CLI Claude (FACTURATION À L'USAGE). "
             "Par défaut ces variables sont retirées pour n'utiliser que l'abonnement.",
    )
    parser.add_argument(
        "--doc-edit",
        action=argparse.BooleanOptionalAction,
        default=DEFAULT_DOC_EDIT,
        help="Mode In-Repo : un agent met à jour la documentation existante du projet (README, CHANGELOG, docs/) dans "
             "le même commit ; toute modification hors documentation est annulée (défaut: activé, --no-doc-edit pour le couper)",
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
        "--json",
        action="store_true",
        help="Sortie machine : le rapport JSON est écrit sur stdout, tout l'affichage humain passe sur stderr",
    )
    parser.add_argument("--no-color", action="store_true", help="Désactiver couleurs et animations (NO_COLOR est aussi respecté)")
    parser.add_argument("--log-file", type=str, default=None, metavar="FICHIER", help="Écrire aussi les logs détaillés dans ce fichier")
    parser.add_argument("-v", "--verbose", action="store_true", help="Activer la journalisation détaillée de débogage")
    return parser


def validate_project_dir(args) -> Optional[str]:
    """Retourne un message d'erreur si `--project-dir` est inutilisable (au lieu d'un passage silencieux en Standalone)."""
    if args.standalone or not args.project_dir:
        return None
    path = Path(args.project_dir)
    if not path.is_dir():
        return f"Le dossier projet '{args.project_dir}' est introuvable."
    if not GitClient(str(path)).is_git_repository():
        return (
            f"'{path}' n'est pas un dépôt Git : le mode In-Repo en a besoin (git init, puis un premier commit). "
            "Pour générer du code sans projet existant, utilisez --standalone."
        )
    return None


# ---------------------------------------------------------------------------
# Affichage du démarrage et du bilan
# ---------------------------------------------------------------------------
def print_banner(args) -> None:
    if args.standalone:
        mode = "Standalone forcé (--standalone)"
    elif args.project_dir:
        mode = f"In-Repo : {args.project_dir}"
    elif GitClient(".").is_git_repository():
        mode = "In-Repo : dossier courant"
    else:
        mode = "Standalone (le dossier courant n'est pas un dépôt Git)"

    print("\n" + "=" * 70)
    print("🚀 ORCHESTRATEUR MULTI-AGENTS CLAUDE & TYPESAFE JEV")
    print("=" * 70)
    print(f"• Prompt initial   : {args.prompt}")
    print(f"• Mode simulation  : {'OUI (--mock)' if args.mock else 'NON (Claude CLI + TypeSafe API)'}")
    print(f"• Mode de travail  : {mode}")
    if args.commit:
        if args.branch:
            print("• Auto-commit Git  : --commit sans effet avec l'isolation par branche (commit systématique sur workflow/ai-*)")
        else:
            print("• Auto-commit Git  : Activé (--commit)")
    print(f"• Isolation branche: {'Activée (--branch)' if args.branch else 'Désactivée (--no-branch)'}")
    print(f"• Doc du projet    : {'mise à jour par un agent (hors code, garde-fou)' if args.doc_edit else 'désactivée (--no-doc-edit)'}")
    print(f"• Tests auto       : {'Activés (--run-tests)' if args.run_tests else 'Désactivés (--no-tests)'}")
    if args.run_tests and (args.test_cmd or TEST_COMMAND):
        print(f"• Commande de test : {args.test_cmd or TEST_COMMAND}")
    print(f"• Outil Bash       : {'Autorisé, liste blanche (--allow-bash)' if args.allow_bash else 'Désactivé'}")
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


def print_summary(report: WorkflowExecutionReport, args) -> None:
    print("\n" + "=" * 70)
    if report.is_success:
        print("✅ WORKFLOW TERMINÉ AVEC SUCCÈS")
    else:
        print(f"⚠️ WORKFLOW INCOMPLET : {report.error_message}")
    print("=" * 70)
    print(f"• Mode effectif    : {'In-Repo (Modifications directes dans le projet)' if report.is_in_repo else 'Standalone (Fichier unique dans le dossier de rapports)'}")
    print(f"• Type de workflow : {report.workflow_type.value if report.workflow_type else 'N/A'}")
    print(f"• Développeur       : {report.dev_specialty.value if report.dev_specialty else 'N/A'}")
    print(f"• Cycles exécutés  : {report.iterations_count}")
    print(f"• Total étapes     : {len(report.history)}")
    if report.cost_usd:
        print(f"• Coût équivalent API : {report.cost_usd:.4f} USD (informatif : l'abonnement n'est pas facturé à l'usage)")
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
        if report.doc_files_kept:
            print(f"• Doc mise à jour  : {', '.join(report.doc_files_kept)}")
        if report.doc_files_reverted:
            print(f"• Hors doc annulé  : {', '.join(report.doc_files_reverted)}")
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
    elif report.code_produit:
        print("\n--- CODE PRODUIT ---")
        print(report.code_produit[:600] + ("\n... [tronqué pour affichage]" if len(report.code_produit) > 600 else ""))

    if report.doc_et_commit:
        print("\n--- DOCUMENTATION & COMMIT GIT ---")
        print(report.doc_et_commit)

    if args.workspace:
        print(f"\n📂 Rapports (audit, report.json, patch) : {Path(args.workspace).resolve()}")
    print("=" * 70 + "\n")


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------
def execute(args, jev_client: JevClient) -> Tuple[int, Optional[WorkflowExecutionReport], Optional[str]]:
    """Lance le workflow. Retourne (code de sortie, rapport éventuel, message d'erreur éventuel)."""
    print_banner(args)

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
        doc_edit=args.doc_edit,
        test_runner=TestRunner(timeout_seconds=args.test_timeout, test_command=args.test_cmd or TEST_COMMAND or None),
    )

    try:
        report = orchestrator.run(args.prompt)
    except KeyboardInterrupt:
        message = "Interrompu (Ctrl-C). Votre dépôt a été remis dans son état d'origine et vos modifications restaurées."
        print(f"\n⛔ {message}", file=sys.stderr)
        return EXIT_INTERRUPTED, None, message
    except JevApiError as e:
        print(f"\n❌ TypeSafe Jev : {e}\nLe workflow a été arrêté (aucune validation par défaut). Votre dépôt a été remis en état.", file=sys.stderr)
        return EXIT_JEV, None, f"TypeSafe Jev : {e}"
    except ClaudeCliError as e:
        print(f"\n❌ Claude CLI : {e}\nVotre dépôt a été remis en état.", file=sys.stderr)
        return EXIT_CLAUDE, None, f"Claude CLI : {e}"
    except WorkflowPreconditionError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        return EXIT_ERROR, None, str(e)
    except Exception as e:
        print(f"\n❌ Erreur fatale durant le workflow : {e}", file=sys.stderr)
        return EXIT_ERROR, None, f"Erreur fatale : {e}"

    print_summary(report, args)
    return (EXIT_OK if report.is_success else EXIT_INCOMPLETE), report, report.error_message


def emit_json(code: int, report: Optional[WorkflowExecutionReport], error: Optional[str]) -> None:
    """Écrit le rapport machine sur stdout (--json) : le rapport complet s'il existe, sinon un résumé d'erreur."""
    payload = report.to_dict() if report is not None else {"is_success": False, "error_message": error}
    payload["exit_code"] = code
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.no_color:
        set_color_enabled(False)
    log_handler = setup_logging(args.verbose, args.log_file)
    try:
        return _main(parser, args)
    finally:
        if log_handler is not None:
            logging.getLogger().removeHandler(log_handler)
            log_handler.close()


def _main(parser: argparse.ArgumentParser, args) -> int:
    if args.doctor:
        return run_doctor(ClaudeCliClient(allow_api_key=args.allow_api_key), allow_api_key=args.allow_api_key, mock=args.mock)

    if not args.prompt:
        parser.error("un prompt est requis, par exemple : workflow --project-dir . \"Ajoute un tri par date dans l'onglet x\"")

    project_error = validate_project_dir(args)
    if project_error:
        print(f"\n❌ {project_error}", file=sys.stderr)
        if args.json:
            emit_json(EXIT_ERROR, None, project_error)
        return EXIT_ERROR

    # Échec rapide : sans clé TypeSafe (et sans --mock), on ne démarre pas (les validations ne seraient pas fiables)
    try:
        jev_client = JevClient(mock_mode=args.mock)
    except JevApiError as e:
        print(f"\n❌ {e}", file=sys.stderr)
        if args.json:
            emit_json(EXIT_JEV, None, str(e))
        return EXIT_JEV

    if args.json:
        with contextlib.redirect_stdout(sys.stderr):  # affichage humain sur stderr, JSON seul sur stdout
            code, report, error = execute(args, jev_client)
        emit_json(code, report, error)
        return code
    code, _report, _error = execute(args, jev_client)
    return code


if __name__ == "__main__":
    sys.exit(main())
