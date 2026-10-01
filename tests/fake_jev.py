"""
Faux serveur TypeSafe System One (http.server dans un thread) reproduisant le format RÉEL de l'API,
relevé contre le service :

  POST /v1/systemone  {"model", "state", "questions": {cle: {type: "choice"|"noul", ...}}}
  200 {"model": "jev-1.13.0", "answers": {cle: {"type": "choice", "choice": ..., "confidence": ..., "probabilities": {...}}
                                          | {"type": "noul", "noul": 0.87}}, "usage": {...}}
  401 {"detail": {"error_type": "authentication_error", "message": ...}}
  400 {"detail": {"error_type": "max_tokens_exceeded"}}
  422 {"detail": [{"type": "missing", "loc": [...], "msg": "Field required"}]}

Le comportement est piloté par une file de « scénarios » : chaque requête consomme le premier élément
(le dernier est répété). Toutes les requêtes reçues sont enregistrées dans `server.requests`.
"""

import json
import threading
import time

_REAL_SLEEP = time.sleep  # référence conservée : les tests patchent time.sleep côté client
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Optional


def ok_noul(value: float, key: str = "is_valid") -> dict:
    return {"status": 200, "body": {"model": "jev-1.13.0", "answers": {key: {"type": "noul", "noul": value}}, "usage": {"input_tokens": 300, "output_tokens": 20}}}


def ok_choice(choice: str, confidence: float = 0.95, key: str = "selection") -> dict:
    return {"status": 200, "body": {"model": "jev-1.13.0", "answers": {key: {"type": "choice", "choice": choice, "confidence": confidence, "probabilities": {choice: confidence}}}, "usage": {"input_tokens": 377, "output_tokens": 53}}}


def error(status: int, detail: Any, headers: Optional[Dict[str, str]] = None) -> dict:
    return {"status": status, "body": {"detail": detail}, "headers": headers or {}}


AUTH_ERROR_401 = error(401, {"error_type": "authentication_error", "message": "Cannot authenticate with the server. Please check your API key and try again."})
MAX_TOKENS_400 = error(400, {"error_type": "max_tokens_exceeded"})


class FakeJevServer:
    def __init__(self):
        self.requests: List[dict] = []
        self.scenarios: List[dict] = [ok_noul(0.9)]
        self._httpd = HTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_address[1]}/v1/systemone"

    def start(self) -> "FakeJevServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def script(self, *scenarios: dict) -> None:
        self.scenarios = list(scenarios)

    def _next(self) -> dict:
        return self.scenarios.pop(0) if len(self.scenarios) > 1 else self.scenarios[0]

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw)
                except ValueError:
                    body = None
                server.requests.append({"path": self.path, "headers": dict(self.headers), "body": body, "raw": raw})

                scenario = server._next()
                if scenario.get("sleep"):
                    _REAL_SLEEP(scenario["sleep"])
                if scenario.get("raw") is not None:
                    payload = scenario["raw"].encode("utf-8")
                else:
                    payload = json.dumps(scenario["body"]).encode("utf-8")
                try:
                    self.send_response(scenario["status"])
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    for k, v in scenario.get("headers", {}).items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        return Handler
