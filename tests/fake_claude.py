"""
Faux binaire `claude` pour les tests du client : lit le prompt sur stdin, enregistre ce qu'il a reçu
(arguments, taille et empreinte du prompt, répertoire courant, variables sensibles) dans un fichier
JSON, puis répond selon FAKE_CLAUDE_MODE.

Variables d'environnement de pilotage (passées par les tests) :
  FAKE_CLAUDE_LOG      chemin du fichier journal JSON (une ligne par appel)
  FAKE_CLAUDE_MODE     ok | error | error_exit0 | plain | sleep | flaky | denials
  FAKE_CLAUDE_MESSAGE  texte du résultat (erreur ou succès)
  FAKE_CLAUDE_STATE    fichier compteur pour le mode flaky (échoue N fois puis réussit)
  FAKE_CLAUDE_FAILS    nombre d'échecs avant succès (mode flaky)
"""

import hashlib
import json
import os
import sys
import time


def main() -> int:
    argv = sys.argv[1:]
    if argv[:1] == ["--version"]:
        print("9.9.9 (Fake Claude)")
        return 0
    if argv[:1] == ["--help"]:
        print("Usage: claude [options]\n  -p, --print\n  --model <model>\n  --output-format <format>\n  --tools <tools...>\n"
              "  --permission-mode <mode>\n  --no-session-persistence\n  --allowedTools <tools...>")
        return 0
    if argv[:2] == ["auth", "status"]:
        logged_in = os.environ.get("FAKE_CLAUDE_LOGGED_IN", "1") == "1"
        print(json.dumps({"loggedIn": logged_in, "authMethod": "claude.ai" if logged_in else "none"}))
        return 0 if logged_in else 1
    if argv[:2] == ["auth", "garbage"]:
        print("pas du json")
        return 0

    stdin_data = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
    message = os.environ.get("FAKE_CLAUDE_MESSAGE", "")

    entry = {
        "argv": argv,
        "stdin_len": len(stdin_data),
        "stdin_sha256": hashlib.sha256(stdin_data.encode("utf-8")).hexdigest(),
        "stdin_head": stdin_data[:80],
        "cwd": os.getcwd(),
        "has_api_key": "ANTHROPIC_API_KEY" in os.environ,
        "has_base_url": "ANTHROPIC_BASE_URL" in os.environ,
    }
    log_path = os.environ.get("FAKE_CLAUDE_LOG")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def emit(result, is_error=False, status=None, **extra):
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": is_error,
            "result": result,
            "api_error_status": status,
            "duration_ms": 1234,
            "num_turns": 3,
            "session_id": "sess-123",
            "total_cost_usd": 0.0042,
            "permission_denials": [],
        }
        payload.update(extra)
        print(json.dumps(payload))

    if mode == "sleep":
        time.sleep(float(os.environ.get("FAKE_CLAUDE_SLEEP", "5")))
        emit("too late")
        return 0
    if mode == "plain":
        print(message or "réponse en texte brut")
        return 0
    if mode == "error":
        emit(message or "boom", is_error=True)
        return 1
    if mode == "error_exit0":
        emit(message or "boom", is_error=True)
        return 0
    if mode == "flaky":
        state = os.environ["FAKE_CLAUDE_STATE"]
        count = int(open(state).read()) if os.path.exists(state) else 0
        with open(state, "w") as f:
            f.write(str(count + 1))
        if count < int(os.environ.get("FAKE_CLAUDE_FAILS", "1")):
            emit(message or "API Error: 529 overloaded_error", is_error=True)
            return 1
        emit("OK après reprise")
        return 0
    if mode == "denials":
        emit("fait", permission_denials=[{"tool_name": "Bash", "tool_input": {"command": "git push"}}])
        return 0

    emit(message or "OK:" + stdin_data[:40])
    return 0


if __name__ == "__main__":
    sys.exit(main())
