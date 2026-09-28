"""
Client API pour le modèle de décision TypeSafe Jev (System One).
Prend en charge l'endpoint officiel TypeSafe (/v1/systemone) ainsi que le
schéma direct (/v1/decide), avec gestion d'erreurs et mode simulation/mock.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional
import requests

from config import (
    MOCK_SERVICES,
    TYPESAFE_API_KEY,
    TYPESAFE_API_URL,
    TYPESAFE_FALLBACK_URL,
    TYPESAFE_TIMEOUT_SECONDS,
)

logger = logging.getLogger("jev_client")


class JevApiError(RuntimeError):
    """Exception levée lors d'un échec de requête vers l'API TypeSafe Jev."""
    pass


class JevClient:
    """
    Client de décision rapide TypeSafe Jev.
    Offre un typage strict et une latence ultra-faible pour le routage et la validation binaire.
    """

    def __init__(
        self,
        api_key: str = TYPESAFE_API_KEY,
        api_url: str = TYPESAFE_API_URL,
        fallback_url: str = TYPESAFE_FALLBACK_URL,
        timeout_seconds: float = TYPESAFE_TIMEOUT_SECONDS,
        mock_mode: bool = MOCK_SERVICES,
    ):
        self.api_key = api_key
        self.api_url = api_url
        self.fallback_url = fallback_url
        self.timeout_seconds = timeout_seconds
        # Activer le mock si explicitement demandé ou si aucune clé n'est configurée
        self.mock_mode = mock_mode or not bool(api_key.strip())

        if not self.mock_mode and not self.api_key:
            logger.warning(
                "Aucune TYPESAFE_API_KEY détectée. Le client Jev fonctionnera en mode simulation."
            )
            self.mock_mode = True

    def classify(self, context: str, choices: List[str], question_label: str = "Choix du workflow") -> str:
        """
        Sélectionne une option parmi un ensemble fini de choix via Jev.
        """
        if self.mock_mode:
            return self._mock_classify(context, choices)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Claude-Jev-Orchestrator/1.0",
        }

        # Tentative 1 : Endpoint officiel TypeSafe System One
        if "systemone" in self.api_url:
            criteria_dict = {c: f"Option: {c}" for c in choices}
            payload = {
                "model": "jev-latest",
                "state": context,
                "questions": {
                    "selection": {
                        "type": "choice",
                        "instructions": question_label,
                        "criteria": criteria_dict,
                    }
                },
            }
            try:
                resp = requests.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    # Structure System One : results.selection.choice
                    res_choice = (
                        data.get("results", {})
                        .get("selection", {})
                        .get("choice")
                    )
                    if res_choice and res_choice in choices:
                        return res_choice
            except requests.RequestException as e:
                logger.warning(f"Échec sur {self.api_url} ({e}), essai sur fallback {self.fallback_url}...")

        # Tentative 2 : Endpoint direct /v1/decide
        payload_decide = {
            "model": "jev",
            "type": "choice",
            "input": context,
            "choices": choices,
        }
        try:
            resp = requests.post(
                self.fallback_url,
                headers=headers,
                json=payload_decide,
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            selected = data.get("selected")
            if selected and selected in choices:
                return selected
            raise JevApiError(f"Réponse inattendue de Jev classify : {data}")
        except requests.RequestException as e:
            raise JevApiError(f"Erreur de communication avec TypeSafe Jev : {e}")

    def validate(self, context: str, criteria: str, threshold: float = 0.5) -> bool:
        """
        Effectue une décision binaire (Validation / Rejet) via Jev (type noul ou binary).
        """
        if self.mock_mode:
            return self._mock_validate(context, criteria)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Claude-Jev-Orchestrator/1.0",
        }

        # Tentative 1 : Endpoint officiel System One (type noul)
        if "systemone" in self.api_url:
            payload = {
                "model": "jev-latest",
                "state": context,
                "questions": {
                    "is_valid": {
                        "type": "noul",
                        "instructions": criteria,
                    }
                },
            }
            try:
                resp = requests.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    prob = (
                        data.get("results", {})
                        .get("is_valid", {})
                        .get("probability", 1.0)
                    )
                    return float(prob) >= threshold
            except requests.RequestException as e:
                logger.warning(f"Échec validation sur {self.api_url} ({e}), essai sur fallback...")

        # Tentative 2 : Endpoint direct /v1/decide (type binary)
        payload_decide = {
            "model": "jev",
            "type": "binary",
            "input": context,
            "criteria": criteria,
        }
        try:
            resp = requests.post(
                self.fallback_url,
                headers=headers,
                json=payload_decide,
                timeout=self.timeout_seconds,
            )
            resp.raise_for_status()
            data = resp.json()
            return bool(data.get("result", True))
        except requests.RequestException as e:
            raise JevApiError(f"Erreur de validation binaire TypeSafe Jev : {e}")

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
            if "node" in ctx_lower or "javascript" in ctx_lower or "typescript" in ctx_lower:
                return "Dev Node.js"
            if "ui" in ctx_lower or "frontend" in ctx_lower or "html" in ctx_lower or "css" in ctx_lower:
                return "Dev UI"
            return "Dev Python"

        return choices[0] if choices else ""

    def _mock_validate(self, context: str, criteria: str) -> bool:
        """Simulation heuristique de validation binaire."""
        ctx_lower = context.lower()

        # Détection de signaux d'anomalie réels (en évitant 'aucun bug', 'aucune faille')
        if "aucun bug" in ctx_lower or "conforme aux exigences" in ctx_lower:
            return True
        if "erreur critique" in ctx_lower or "faille critique" in ctx_lower or "vulnérabilité critique" in ctx_lower:
            return False
        if "code non conforme" in ctx_lower or "rejet obligatoire" in ctx_lower:
            return False

        # Par défaut, validation accordée
        return True
