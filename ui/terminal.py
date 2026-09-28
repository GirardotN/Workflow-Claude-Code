"""
Utilitaires d'affichage terminal sans dépendance externe (Zero Bloatware).
Propose un spinner animé non-bloquant, la coloration syntaxique ANSI du diff Git
et des invites de confirmation interactives.
"""

import os
import sys
import threading
import time
from typing import Optional

# Codes de couleurs ANSI standards
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"


def is_ansi_supported() -> bool:
    """Détecte si la sortie standard supporte les codes d'échappement ANSI."""
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if os.name == "nt":
        # Windows 10+ avec WT_SESSION ou ConEmu ou ANSICON
        return "WT_SESSION" in os.environ or "ANSICON" in os.environ or os.environ.get("TERM") == "xterm"
    return True


class Spinner:
    """
    Indicateur visuel dynamique exécuté dans un thread séparé en tâche de fond.
    S'arrête automatiquement et proprement à la sortie du bloc `with`.
    """

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = "Traitement en cours..."):
        self.message = message
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.start_time: float = 0.0
        self.interactive = is_ansi_supported()

    def start(self):
        self.start_time = time.perf_counter()
        if not self.interactive:
            # Mode non-interactif (CI, pipe, fichier) : affichage simple d'une ligne
            print(f"  ⏳ {self.message}...", flush=True)
            return

        self.running = True
        self.thread = threading.Thread(target=self._spin, daemon=True)
        self.thread.start()

    def _spin(self):
        idx = 0
        while self.running:
            frame = self.FRAMES[idx % len(self.FRAMES)]
            elapsed = time.perf_counter() - self.start_time
            sys.stdout.write(f"\r  {CYAN}{frame}{RESET} {self.message} {DIM}({elapsed:.1f}s){RESET}")
            sys.stdout.flush()
            idx += 1
            time.sleep(0.08)

    def stop(self, success_message: Optional[str] = None):
        if not self.running:
            return
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.3)

        elapsed = time.perf_counter() - self.start_time
        if self.interactive:
            sys.stdout.write("\r" + " " * 80 + "\r")
            msg = success_message or self.message
            sys.stdout.write(f"  {GREEN}✔{RESET} {msg} {DIM}({elapsed:.2f}s){RESET}\n")
            sys.stdout.flush()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.running = False
            if self.interactive:
                sys.stdout.write("\r" + " " * 80 + "\r")
                sys.stdout.write(f"  {RED}✖{RESET} {self.message} {RED}[ÉCHEC]{RESET}\n")
                sys.stdout.flush()
        else:
            self.stop()


def format_colored_diff(diff_text: str) -> str:
    """
    Applique la coloration syntaxique ANSI au patch unifié `git diff`.
    Vert pour les ajouts (+), rouge pour les suppressions (-), cyan pour les blocs (@@).
    """
    if not diff_text or not is_ansi_supported():
        return diff_text

    colored_lines = []
    for line in diff_text.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            colored_lines.append(f"{BOLD}{line}{RESET}")
        elif line.startswith("+"):
            colored_lines.append(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            colored_lines.append(f"{RED}{line}{RESET}")
        elif line.startswith("@@"):
            colored_lines.append(f"{CYAN}{line}{RESET}")
        elif line.startswith("diff --git"):
            colored_lines.append(f"\n{BOLD}{YELLOW}{line}{RESET}")
        else:
            colored_lines.append(line)

    return "\n".join(colored_lines)


def confirm_action(prompt: str, default: bool = False) -> bool:
    """
    Demande une confirmation interactive [y/N] ou [Y/n] à l'utilisateur.
    Retourne la valeur par défaut si le terminal est non-interactif.
    """
    choices = "[Y/n]" if default else "[y/N]"
    prompt_full = f"{prompt} {choices} : "

    if not sys.stdin.isatty():
        return default

    try:
        reply = input(prompt_full).strip().lower()
        if not reply:
            return default
        return reply in ("y", "yes", "o", "oui")
    except (EOFError, KeyboardInterrupt):
        print()
        return default
