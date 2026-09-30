"""
Client API pour le modèle de décision TypeSafe Jev (endpoint System One : POST /v1/systemone).

Format réel de l'API (vérifié contre le service) :
  requête   {"model": "jev-latest", "state": "<texte>", "questions": {"<clé>": {...}}}
  choix     question {"type": "choice", "instructions": "...", "criteria": {"<option>": "<description>"}}
            réponse  answers.<clé> = {"type": "choice", "choice": "<option>", "confidence": 0.98, "probabilities": {...}}
  validation question {"type": "noul", "instructions": "..."}
            réponse  answers.<clé> = {"type": "noul", "noul": 0.87}   (probabilité que la réponse soit « oui »)
  erreurs   401/403 authentification · 400 requête invalide ou `max_tokens_exceeded` (~32 000 tokens) · 422 schéma.

Principe de sûreté : FAIL-CLOSED. Une réponse absente, invalide ou inattendue lève JevApiError : elle
ne vaut jamais « validé ». Le mode simulation n'existe que sur demande explicite (`mock_mode=True`).
"""

import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..config import (
    JEV_MAX_RETRIES,
    JEV_MAX_STATE_CHARS,
    JEV_THRESHOLD,
    MOCK_SERVICES,
    TYPESAFE_API_KEY,
    TYPESAFE_API_URL,
    TYPESAFE_TIMEOUT_SECONDS,
)
from ..jev_context import truncate_middle

logger = logging.getLogger("jev_client")

MODEL = "jev-latest"


class JevApiError(RuntimeError):
    """Échec d'une requête vers TypeSafe Jev (réseau, service, réponse invalide). Jamais un « validé »."""
    pass


class JevAuthError(JevApiError):
    """Clé API refusée ou absente côté service (HTTP 401/403)."""
    pass


class JevConfigError(JevApiError):
    """Configuration locale inutilisable : clé TypeSafe absente."""
    pass


@dataclass
class JevDecision:
    """Trace d'une décision Jev (pour le rapport et l'audit)."""
    kind: str                         # "choice" | "noul"
    mode: str                         # "live" | "mock"
    question: str = ""
    result: Any = None                # option choisie, ou booléen (validé / rejeté)
    value: Optional[float] = None     # confidence (choice) ou noul (validation)
    threshold: Optional[float] = None
    probabilities: Dict[str, float] = field(default_factory=dict)
    latency_ms: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    attempts: int = 1
    state_chars: int = 0
    truncated: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _is_placeholder(key: str) -> bool:
    return (not key) or key.lower().startswith("votre_") or key.lower() in {"changeme", "your_key_here"}


class JevClient:
    """
    Client de décision rapide TypeSafe Jev : routage (choice) et validation binaire probabiliste (noul).
    """

    supports_descriptions = True  # `classify` accepte des descriptions d'options

    def __init__(
        self,
        api_key: str = TYPESAFE_API_KEY,
        api_url: str = TYPESAFE_API_URL,
        timeout_seconds: float = TYPESAFE_TIMEOUT_SECONDS,
        mock_mode: bool = MOCK_SERVICES,
        max_retries: int = JEV_MAX_RETRIES,
        threshold: float = JEV_THRESHOLD,
        max_state_chars: int = JEV_MAX_STATE_CHARS,
        session: Optional[requests.Session] = None,
    ):
        self.api_key = (api_key or "").strip()
        self.api_url = api_url
        self.timeout_seconds = timeout_seconds
        self.mock_mode = mock_mode
        self.max_retries = max(0, max_retries)
        self.threshold = threshold
        self.max_state_chars = max_state_chars
        self.last_decision: Optional[JevDecision] = None
        self._session = session or requests.Session()

        if not self.mock_mode and _is_placeholder(self.api_key):
            raise JevConfigError(
                "Clé TypeSafe absente : définissez TYPESAFE_API_KEY (fichier .env ou variable d'environnement), "
                "ou lancez avec --mock pour une simulation (validations NON fiables)."
            )

    @property
    def mode(self) -> str:
        return "mock" if self.mock_mode else "live"

    # ------------------------------------------------------------------
    # API publique
    # ------------------------------------------------------------------
    def classify(
        self,
        context: str,
        choices: List[str],
        question_label: str = "Choix du workflow",
        descriptions: Optional[Dict[str, str]] = None,
    ) -> str:
        """Sélectionne une option parmi un ensemble fini de choix via Jev."""
        if self.mock_mode:
            choice = self._mock_classify(context, choices)
            self.last_decision = JevDecision(kind="choice", mode="mock", question=question_label, result=choice)
            return choice

        criteria = {c: (descriptions or {}).get(c, f"Option: {c}") for c in choices}
        questions = {"selection": {"type": "choice", "instructions": question_label, "criteria": criteria}}
        answers, meta = self._post(questions, context)

        answer = answers.get("selection")
        choice = answer.get("choice") if isinstance(answer, dict) else None
        if choice not in choices:
            raise JevApiError(f"Réponse de choix inattendue de Jev : {self._brief(answer)} (options : {choices})")

        confidence = answer.get("confidence")
        probabilities = answer.get("probabilities")
        self.last_decision = JevDecision(
            kind="choice",
            mode="live",
            question=question_label,
            result=choice,
            value=float(confidence) if isinstance(confidence, (int, float)) else None,
            probabilities={k: float(v) for k, v in probabilities.items()} if isinstance(probabilities, dict) else {},
            **meta,
        )
        return choice

    def validate(self, context: str, criteria: str, threshold: Optional[float] = None) -> bool:
        """
        Décision binaire via Jev (question de type `noul`).
        Retourne True si la probabilité `noul` de répondre « oui » atteint le seuil.
        Lève JevApiError si la réponse est absente ou invalide (jamais de validation par défaut).
        """
        limit = self.threshold if threshold is None else threshold
        if self.mock_mode:
            ok = self._mock_validate(context, criteria)
            self.last_decision = JevDecision(kind="noul", mode="mock", question=criteria, result=ok, threshold=limit)
            return ok

        answers, meta = self._post({"is_valid": {"type": "noul", "instructions": criteria}}, context)

        answer = answers.get("is_valid")
        noul = answer.get("noul") if isinstance(answer, dict) else None
        if isinstance(noul, bool) or not isinstance(noul, (int, float)) or not 0.0 <= float(noul) <= 1.0:
            raise JevApiError(f"Réponse de validation inattendue de Jev (champ 'noul' absent ou hors [0,1]) : {self._brief(answer)}")

        valid = float(noul) >= limit
        self.last_decision = JevDecision(
            kind="noul", mode="live", question=criteria, result=valid, value=float(noul), threshold=limit, **meta
        )
        return valid

    def ping(self) -> JevDecision:
        """Appel minimal (quelques dizaines de tokens) pour vérifier la clé et la joignabilité du service."""
        if self.mock_mode:
            return JevDecision(kind="noul", mode="mock")
        self.validate("Test de connectivité : 1 + 1 = 2.", "Cette égalité est-elle correcte ?")
        return self.last_decision  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
    def _post(self, questions: Dict[str, Any], state: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """POST vers System One avec reprises sur erreurs transitoires. Retourne (answers, métadonnées)."""
        original_len = len(state or "")
        state = truncate_middle(state or "", self.max_state_chars)
        payload = {"model": MODEL, "state": state, "questions": questions}
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "workflow-claude-code/jev-client",
        }
        attempts = 1 + self.max_retries
        start = time.perf_counter()

        for attempt in range(1, attempts + 1):
            try:
                resp = self._session.post(self.api_url, headers=headers, json=payload, timeout=self.timeout_seconds)
            except requests.RequestException as e:
                if attempt == attempts:
                    raise JevApiError(
                        f"Erreur de communication avec TypeSafe Jev après {attempts} tentative(s) : {type(e).__name__}"
                    ) from None
                self._backoff(attempt, None, f"erreur réseau ({type(e).__name__})")
                continue

            status = resp.status_code
            if status == 200:
                answers = self._parse_answers(resp)
                usage = resp.json().get("usage", {}) if isinstance(resp.json(), dict) else {}
                meta = {
                    "latency_ms": int((time.perf_counter() - start) * 1000),
                    "input_tokens": usage.get("input_tokens") if isinstance(usage, dict) else None,
                    "output_tokens": usage.get("output_tokens") if isinstance(usage, dict) else None,
                    "attempts": attempt,
                    "state_chars": len(state),
                    "truncated": len(state) < original_len,
                }
                return answers, meta

            detail = self._error_detail(resp)
            if status in (401, 403):
                raise JevAuthError(f"Clé TypeSafe refusée (HTTP {status}) : {detail}. Vérifiez TYPESAFE_API_KEY.")
            if status == 429 or status >= 500:
                if attempt == attempts:
                    raise JevApiError(f"TypeSafe Jev indisponible (HTTP {status}) après {attempts} tentative(s) : {detail}")
                self._backoff(attempt, resp.headers.get("Retry-After"), f"HTTP {status}")
                continue
            raise JevApiError(f"Requête refusée par TypeSafe Jev (HTTP {status}) : {detail}")

        raise JevApiError("Échec inattendu de la requête vers TypeSafe Jev.")  # pragma: no cover

    def _backoff(self, attempt: int, retry_after: Optional[str], reason: str) -> None:
        delay = float(2 ** (attempt - 1))
        if retry_after:
            try:
                delay = min(max(float(retry_after), 0.0), 30.0)
            except ValueError:
                pass
        logger.warning(f"TypeSafe Jev : {reason}. Nouvelle tentative {attempt}/{self.max_retries} dans {delay:.0f}s...")
        time.sleep(delay)

    @staticmethod
    def _parse_answers(resp: requests.Response) -> Dict[str, Any]:
        try:
            data = resp.json()
        except ValueError:
            raise JevApiError("Réponse de TypeSafe Jev illisible (JSON invalide).") from None
        answers = data.get("answers") if isinstance(data, dict) else None
        if not isinstance(answers, dict):
            keys = sorted(data.keys()) if isinstance(data, dict) else type(data).__name__
            raise JevApiError(f"Réponse de TypeSafe Jev inattendue : clé 'answers' absente (reçu : {keys}).")
        return answers

    def _error_detail(self, resp: requests.Response) -> str:
        """Résume le corps d'erreur (sans jamais y laisser la clé)."""
        try:
            detail = resp.json().get("detail")
        except (ValueError, AttributeError):
            detail = resp.text[:200]
        if isinstance(detail, dict):
            text = ": ".join(str(detail[k]) for k in ("error_type", "message") if k in detail) or str(detail)
        elif isinstance(detail, list) and detail and isinstance(detail[0], dict):
            first = detail[0]
            text = f"{first.get('msg', first)} ({'.'.join(str(p) for p in first.get('loc', []))})"
        else:
            text = str(detail)
        return text.replace(self.api_key, "<KEY>") if self.api_key else text

    @staticmethod
    def _brief(value: Any, limit: int = 200) -> str:
        return str(value)[:limit]

    # ------------------------------------------------------------------
    # Simulation (uniquement avec mock_mode=True / --mock)
    # ------------------------------------------------------------------
    def _mock_classify(self, context: str, choices: List[str]) -> str:
        """Simulation heuristique pour choix."""
        ctx_lower = context.lower()

        # Classification de complexité
        if "tâche simple" in [c.lower() for c in choices]:
            if "complexe" in ctx_lower or "architecture" in ctx_lower or "multi-agent" in ctx_lower:
                return "Tâche Complexe"
            if "moyen" in ctx_lower or "sécurité" in ctx_lower or "api" in ctx_lower:
                return "Tâche Moyenne"
            return "Tâche Simple"

        # Classification de spécialité dev
        if "dev c#" in [c.lower() for c in choices]:
            if "c#" in ctx_lower or ".net" in ctx_lower:
                return "Dev C#"
            if re.search(r"\b(node|nodejs|js|javascript|typescript)\b", ctx_lower):
                return "Dev Node.js"
            if re.search(r"\b(ui|frontend|front-end|react|vue|html|css)\b", ctx_lower):
                return "Dev UI"
            return "Dev Python"

        return choices[0] if choices else ""

    def _mock_validate(self, context: str, criteria: str) -> bool:
        """Simulation heuristique de validation binaire."""
        ctx_lower = context.lower()

        # 1. Détection prioritaire des signaux d'anomalie réels et rejets bloquants
        rejection_signals = [
            "erreur critique",
            "faille critique",
            "vulnérabilité critique",
            "code non conforme",
            "rejet obligatoire",
        ]
        if any(signal in ctx_lower for signal in rejection_signals):
            return False

        # 2. Validation si signaux positifs présents
        if "aucun bug" in ctx_lower or "conforme aux exigences" in ctx_lower:
            return True

        # Par défaut, validation accordée
        return True
