# Spécification Technique & Proposition d'Implémentation : Orchestration Multi-Agents Claude & Jev (TypeSafe)

> **Destinataire :** Agent IA / Assistant au sein de l'IDE.  
> **Mission :** Examiner le cahier des charges, analyser la piste technique proposée (machine à états pilotant le CLI Claude Code et l'API Jev), valider sa faisabilité ou proposer des ajustements/alternatives plus robustes pour l'implémentation.

---

## 1. Contexte & Contraintes Techniques Non Négociables

1. **Zéro crédit d'API Claude (Anthropic API développeur) :**
   - L'utilisateur dispose d'un abonnement **Claude Max 5x** (qui inclut l'usage illimité dans la limite des quotas de session du CLI **Claude Code**).
   - **Interdiction absolue** d'instancier le SDK Anthropic avec une clé payante `ANTHROPIC_API_KEY`.
   - Tout appel à un modèle Claude (Sonnet, Opus, Haiku) doit être délégué localement à la session active de Claude Code (ex: mode headless via terminal `claude -p "<prompt>" --model <model>`).

2. **Utilisation autorisée des crédits TypeSafe (Jev) :**
   - Le modèle décisionnel **Jev** développé par TypeSafe est utilisé pour le routage et les décisions binaires de validation.
   - Les appels HTTP vers l'API de TypeSafe sont autorisés et ne violent pas la règle d'interdiction de crédits Claude.

3. **Isolation et strict respect des contextes :**
   - Pour éviter de faire exploser la consommation de tokens et respecter le schéma :
     - Le check qualité/bug ne reçoit **que** le résultat produit par le dev.
     - Le check sécurité reçoit **le résultat ET la review qualité**.
     - Les prompts de retour doivent être concis pour re-nourrir le dev sans historique superflu.

---

## 2. Description Verbatim du Workflow (Diagramme Source)

Le workflow décrit dans le diagramme original se décompose rigoureusement ainsi :

```
                                  [Prompt simple]
                                         │
                                         ▼
                         [Création prompt complexes (Sonnet)]
                                         │
                                         ▼
                     [Choix du workflow par JEV pour chaque étapes]
                                         │
            ┌────────────────────────────┼────────────────────────────┐
            ▼                            ▼                            ▼
      [Tâche Simple]              [Tâche Moyenne]              [Tâche Complexe]
```

### 2.1 Branche : Tâche Simple
1. **Choix du sous-agent de dev par JEV** (ex: *Dev C# / Node js / UI / ...*).
2. **Envoie du prompt au dev** *(Modèle : Sonnet)*.
3. **Envoie du résultat seulement au check bug / qualité** *(Modèle : Sonnet)*.
4. **Jev décide si on valide ou pas coté bug et qualité** :
   - *Si Rejet :* **Création prompt de retour** *(Modèle : Haiku)* $\longrightarrow$ Boucle vers l'étape 2 (Envoie du prompt au dev - Sonnet).
   - *Si Validation :* **Sonnet crée / met à jour la doc et crée le commit** *(Modèle : Sonnet)* $\longrightarrow$ Fin.

---

### 2.2 Branche : Tâche Moyenne
1. **Choix du sous-agent de dev par JEV** (ex: *Dev C# / Node js / UI / ...*).
2. **Envoie du prompt au dev** *(Modèle : Sonnet)*.
3. **Envoie du résultat seulement au check bug / qualité** *(Modèle : Sonnet)*.
4. **Jev décide si on valide ou pas coté bug et qualité** :
   - *Si Rejet :* **Création prompt de retour** *(Modèle : Haiku)* $\longrightarrow$ Boucle vers l'étape 2 (Envoie du prompt au dev - Sonnet).
   - *Si Validation :* **Envoie du résultat et de la review à l'agent check sécurité** *(Modèle : Sonnet)*.
5. **Jev décide si on valide ou pas coté sécurité** :
   - *Si Rejet :* **Création prompt de retour** *(Modèle : Haiku)* $\longrightarrow$ Boucle vers l'étape 2 (Envoie du prompt au dev - Sonnet).  
     *(Remarque : repasse obligatoirement par le check bug/qualité après re-développement)*.
   - *Si Validation :* **Création / mise à jour la doc et création du commit** *(Modèle : Haiku)* $\longrightarrow$ Fin.

---

### 2.3 Branche : Tâche Complexe
1. **Choix du sous-agent de dev par JEV** (ex: *Dev C# / Node js / UI / ...*).
2. **Envoie du prompt au dev** *(Modèle : Opus)*.
3. **Envoie du résultat seulement au check bug / qualité** *(Modèle : Opus)*.
4. **Jev décide si on valide ou pas coté bug et qualité** :
   - *Si Rejet :* **Création prompt de retour** *(Modèle : Sonnet)* $\longrightarrow$ Boucle vers l'étape 2 (Envoie du prompt au dev - Opus).
   - *Si Validation :* **Envoie du résultat et de la review à l'agent check sécurité** *(Modèle : Sonnet)*.
5. **Jev décide si on valide ou pas coté sécurité** :
   - *Si Rejet :* **Création prompt de retour** *(Modèle : Sonnet)* $\longrightarrow$ Boucle vers l'étape 2 (Envoie du prompt au dev - Opus).
   - *Si Validation :* **Haiku crée / met à jour la doc et crée le commit** *(Modèle : Haiku)* $\longrightarrow$ Fin.

---

## 3. Matrice Récapitulative des Rôles et Modèles

| Étape / Rôle | Tâche Simple | Tâche Moyenne | Tâche Complexe |
| :--- | :--- | :--- | :--- |
| **Génération spec complexe** | Sonnet | Sonnet | Sonnet |
| **Routage complexité & Choix dev** | Jev (Choice) | Jev (Choice) | Jev (Choice) |
| **Agent de Développement** | **Sonnet** | **Sonnet** | **Opus** |
| **Check Bug / Qualité** | **Sonnet** | **Sonnet** | **Opus** |
| **Décision Bug / Qualité** | Jev (Binary) | Jev (Binary) | Jev (Binary) |
| **Prompt de retour (Bug)** | **Haiku** | **Haiku** | **Sonnet** |
| **Check Sécurité** | *(Non exécuté)* | **Sonnet** | **Sonnet** |
| **Décision Sécurité** | *(Non exécuté)* | Jev (Binary) | Jev (Binary) |
| **Prompt de retour (Sécurité)**| *(Non exécuté)* | **Haiku** | **Sonnet** |
| **Doc & Commit Git** | **Sonnet** | **Haiku** | **Haiku** |

---

## 4. Proposition d'Architecture Technique (Piste à valider/ajuster)

L'approche envisagée repose sur un orchestrateur externe sous forme de **machine à états (Python ou Node.js)** exécuté en local. Il pilote :
1. Le binaire `claude` (Claude Code CLI) via des subprocess non-interactifs (`-p`).
2. L'API REST TypeSafe pour le modèle de décision Jev.

### Prototype proposé pour analyse :

```python
import os
import subprocess
import json
import httpx
from dotenv import load_dotenv

load_dotenv()
TYPESAFE_API_KEY = os.getenv("TYPESAFE_API_KEY")
JEV_API_URL = "https://api.typesafe.ai/v1/decide"

def run_claude_cli(prompt: str, model: str) -> str:
    """
    Exécute Claude Code via la session CLI locale OAuth (compte Max 5x).
    N'utilise aucun token payant de l'API développeur Anthropic.
    """
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "json"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(proc.stdout)
        return data.get("result", proc.stdout)
    except Exception:
        # Fallback en mode texte direct
        cmd_fallback = ["claude", "-p", prompt, "--model", model]
        proc = subprocess.run(cmd_fallback, capture_output=True, text=True, check=True)
        return proc.stdout.strip()

def jev_classify(context: str, choices: list[str]) -> str:
    """Sélection d'option via TypeSafe / Jev."""
    headers = {"Authorization": f"Bearer {TYPESAFE_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "jev", "type": "choice", "input": context, "choices": choices}
    with httpx.Client() as client:
        res = client.post(JEV_API_URL, json=payload, headers=headers, timeout=30.0)
        res.raise_for_status()
        return res.json()["selected"]

def jev_validate(context: str, criteria: str) -> bool:
    """Décision binaire (True/False) via TypeSafe / Jev."""
    headers = {"Authorization": f"Bearer {TYPESAFE_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": "jev", "type": "binary", "input": context, "criteria": criteria}
    with httpx.Client() as client:
        res = client.post(JEV_API_URL, json=payload, headers=headers, timeout=30.0)
        res.raise_for_status()
        return res.json()["result"]

def run_workflow(prompt_simple: str):
    # 1. Spécification
    prompt_complexe = run_claude_cli(
        f"Génère une spécification technique détaillée à partir de : {prompt_simple}",
        model="sonnet"
    )

    # 2. Aiguillage initial par JEV
    workflow_type = jev_classify(prompt_complexe, ["Tâche Simple", "Tâche Moyenne", "Tâche Complexe"])
    dev_specialty = jev_classify(prompt_complexe, ["Dev C#", "Dev Node.js", "Dev UI", "Dev Python"])

    # Configuration des modèles
    is_complexe = (workflow_type == "Tâche Complexe")
    is_simple = (workflow_type == "Tâche Simple")

    dev_model = "opus" if is_complexe else "sonnet"
    quality_model = "opus" if is_complexe else "sonnet"
    feedback_model = "sonnet" if is_complexe else "haiku"
    doc_commit_model = "sonnet" if is_simple else "haiku"

    # Machine à états
    etape = "DEV"
    code_produit = ""
    review_qualite = ""
    dernier_feedback = ""
    iter_count = 0
    MAX_RETRIES = 4

    while etape != "DOC_ET_COMMIT" and iter_count < MAX_RETRIES:
        iter_count += 1

        if etape == "DEV":
            if not dernier_feedback:
                p = f"Rôle : {dev_specialty}.\nCahier des charges :\n{prompt_complexe}"
            else:
                p = (f"Rôle : {dev_specialty}.\nSpécification :\n{prompt_complexe}\n\n"
                     f"Code existant :\n{code_produit}\n\nRetours obligatoires :\n{dernier_feedback}")
            code_produit = run_claude_cli(p, model=dev_model)
            dernier_feedback = ""
            etape = "CHECK_QUALITE"

        elif etape == "CHECK_QUALITE":
            # Uniquement le résultat transmis
            review_qualite = run_claude_cli(
                f"Analyse uniquement ce code (qualité et bugs) :\n{code_produit}",
                model=quality_model
            )
            valide = jev_validate(
                f"Code :\n{code_produit}\n\nReview :\n{review_qualite}",
                "Le code est-il exempt de bugs et conforme ?"
            )
            if not valide:
                dernier_feedback = run_claude_cli(
                    f"Rédige le feedback correctif :\n{review_qualite}",
                    model=feedback_model
                )
                etape = "DEV"
            else:
                etape = "DOC_ET_COMMIT" if is_simple else "CHECK_SECURITE"

        elif etape == "CHECK_SECURITE":
            # Résultat ET review qualité transmis
            review_securite = run_claude_cli(
                f"Analyse de sécurité sur ce code :\n{code_produit}\n\nReview qualité préalable :\n{review_qualite}",
                model="sonnet"
            )
            valide_secu = jev_validate(
                f"Code :\n{code_produit}\n\nReview Sécurité :\n{review_securite}",
                "Le code est-il parfaitement sécurisé ?"
            )
            if not valide_secu:
                dernier_feedback = run_claude_cli(
                    f"Rédige le feedback correctif de sécurité :\n{review_securite}",
                    model=feedback_model
                )
                etape = "DEV" # Remonte au dev -> repassera par CHECK_QUALITE
            else:
                etape = "DOC_ET_COMMIT"

    # Finalisation
    return run_claude_cli(
        f"Génère la documentation et le commit pour ce code validé :\n{code_produit}",
        model=doc_commit_model
    )
```

---

## 5. Directives pour l'Agent IA de l'IDE

1. **Évaluation critique :**
   - Cette solution basée sur des appels CLI `claude -p` est-elle la meilleure pour notre environnement, ou devrions-nous plutôt structurer ce workflow via des sous-agents natifs dans `.claude/agents/` combinés à un serveur MCP local pour Jev ?
   - Quels sont les risques de latence ou de formats de sortie avec les subprocess ? Comment rendre la capture de la sortie de code plus robuste ?
2. **Garde-fous :**
   - Proposer une gestion de contexte pour éviter que le code ne grossisse de manière incontrôlée lors des allers-retours de feedback.
   - S'assurer que le système de persistance (fichiers modifiés sur disque vs chaîne de caractères en mémoire) est bien aligné avec le fonctionnement du projet.
3. **Mise en œuvre :**
   - Implémenter ou corriger ce module en respectant rigoureusement chaque nœud du workflow décrit au point 2.