# 🤖 Workflow-Claude-Code : Orchestrateur Multi-Agents Claude & TypeSafe Jev

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)](https://github.com/GirardotN/Workflow-Claude-Code)
[![Claude Code CLI](https://img.shields.io/badge/Claude_Code-Headless_CLI-6B46C1.svg)](https://docs.anthropic.com/en/docs/agents-and-tools/claude-code/overview)
[![TypeSafe Jev](https://img.shields.io/badge/TypeSafe-Jev_System_One-00C7B7.svg)](https://typesafe.ai)
[![Zero API Token Claude](https://img.shields.io/badge/Claude_API_Credits-0_Utilis%C3%A9-brightgreen.svg)](#-contraintes-fondamentales--z%C3%A9ro-cr%C3%A9dit-api-claude)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Orchestrateur multi-agents autonome sous forme de machine à états finis, conçu pour automatiser le cycle complet de conception logicielle (spécification technique, développement spécialisé, contrôle qualité, audit de sécurité, documentation et message de commit Git).

Le projet tire parti des modèles de pointe **Claude** (Opus, Sonnet, Haiku) via le CLI local **Claude Code** en mode headless (`claude -p`), tout en déléguant le routage dynamique et les décisions de validation binaires au moteur décisionnel haute performance **TypeSafe Jev**.

Le code est **100 % multiplateforme** et fonctionne de manière transparente sous **Linux**, **macOS** et **Windows**.

---

## 🎯 Contraintes Fondamentales & Architecture

### 1. Zéro crédit d'API Claude Développeur
* **Principe :** L'utilisateur dispose d'un abonnement **Claude Max 5x** offrant un usage illimité (dans les limites de session) du CLI **Claude Code**.
* **Implémentation :** Aucune clé `ANTHROPIC_API_KEY` payante n'est nécessaire ni instanciée. Toutes les sollicitations de Claude sont exécutées via des sous-processus locaux non-interactifs (`claude -p "<prompt>" --model <model>`).

### 2. Aiguillage & Décisions Rapides via TypeSafe Jev
* **Principe :** Les arbitrages fins (choix du niveau de complexité, sélection de la spécialité technique du développeur, validation/rejet binaire qualité et sécurité) sont confiés au modèle décisionnel **Jev** (TypeSafe System One / Decide).
* **Avantage :** Latence ultra-faible, format de réponse déterministe (classification `choice` ou probabilité binaire `noul`/`binary`) et économie substantielle de tokens.

### 3. Isolation Stricte des Contextes & Préservation des Tokens
Pour éviter tout biais de confirmation et empêcher l'explosion de la fenêtre de contexte :
* L'**Agent Qualité** ne reçoit **que** le code source généré (aucune vue sur la spécialité ou la spec initiale).
* L'**Agent Sécurité** reçoit **le code source ET la review qualité** préalable.
* Les **Prompts de feedback correctifs** sont synthétisés sous forme de listes à puces concises pour réinjecter le strict minimum nécessaire au développeur en cas de rejet.

### 4. Garde-fou Anti-Boucle (Circuit Breaker)
* Un compteur de cycles (`MAX_WORKFLOW_RETRIES`) plafonne le nombre d'allers-retours entre le développement et les revues en cas de rejet persistant, évitant ainsi toute consommation incontrôlée.

### 5. Compatibilité Multiplateforme Native (Linux, macOS, Windows)
* **Windows CMD / PowerShell :** Prise en charge native des scripts batch npm (`claude.cmd`) via `shell=True` automatique sous Windows.
* **Encodage UTF-8 universel :** Décodage explicite en UTF-8 avec gestion des erreurs de remplacement pour éviter les erreurs `UnicodeDecodeError` (page de code Windows CP1252 / CP850).
* **Reconfiguration console :** `sys.stdout` et `sys.stderr` sont configurés pour afficher correctement les caractères accentués et les emojis sous les consoles Windows (cmd.exe, PowerShell, Windows Terminal).
* **Détection automatique des chemins :** Recherche intelligente du binaire `claude` dans le `PATH`, les répertoires npm Linux/macOS ainsi que `%APPDATA%\npm`, `%LOCALAPPDATA%` et `Program Files`.
* **Chargement résilient du `.env` :** Supporte `python-dotenv` tout en intégrant un parseur de secours natif sans aucune dépendance externe requise.

---

## 🔄 Diagramme du Workflow Multi-Agents

```mermaid
flowchart TD
    Start([Prompt Utilisateur Simple]) --> Spec[Étape 1 : Spécification Technique Complète<br/><i>Modèle : Claude Sonnet</i>]
    Spec --> JevRoute{Étape 2 : Aiguillage Jev<br/><i>Niveau de complexité & Spécialité Dev</i>}

    %% Branche Simple
    JevRoute -->|Tâche Simple| DevSimple[Dev Simple<br/><i>Modèle : Sonnet</i>]
    DevSimple --> QualSimple[Check Bug & Qualité<br/><i>Modèle : Sonnet</i>]
    QualSimple --> JevQualSimple{Jev : Validation Qualité ?}
    JevQualSimple -->|Rejet| FbQualSimple[Feedback Correctif Qualité<br/><i>Modèle : Haiku</i>]
    FbQualSimple --> DevSimple
    JevQualSimple -->|Validé| DocSimple[Doc & Commit Git<br/><i>Modèle : Sonnet</i>]

    %% Branche Moyenne
    JevRoute -->|Tâche Moyenne| DevMoy[Dev Moyen<br/><i>Modèle : Sonnet</i>]
    DevMoy --> QualMoy[Check Bug & Qualité<br/><i>Modèle : Sonnet</i>]
    QualMoy --> JevQualMoy{Jev : Validation Qualité ?}
    JevQualMoy -->|Rejet| FbQualMoy[Feedback Correctif Qualité<br/><i>Modèle : Haiku</i>]
    FbQualMoy --> DevMoy
    JevQualMoy -->|Validé| SecuMoy[Check Sécurité<br/><i>Modèle : Sonnet</i>]
    SecuMoy --> JevSecuMoy{Jev : Validation Sécurité ?}
    JevSecuMoy -->|Rejet| FbSecuMoy[Feedback Correctif Sécu<br/><i>Modèle : Haiku</i>]
    FbSecuMoy --> DevMoy
    JevSecuMoy -->|Validé| DocMoy[Doc & Commit Git<br/><i>Modèle : Haiku</i>]

    %% Branche Complexe
    JevRoute -->|Tâche Complexe| DevComp[Dev Complexe<br/><i>Modèle : Opus</i>]
    DevComp --> QualComp[Check Bug & Qualité<br/><i>Modèle : Opus</i>]
    QualComp --> JevQualComp{Jev : Validation Qualité ?}
    JevQualComp -->|Rejet| FbQualComp[Feedback Correctif Qualité<br/><i>Modèle : Sonnet</i>]
    FbQualComp --> DevComp
    JevQualComp -->|Validé| SecuComp[Check Sécurité<br/><i>Modèle : Sonnet</i>]
    SecuComp --> JevSecuComp{Jev : Validation Sécurité ?}
    JevSecuComp -->|Rejet| FbSecuComp[Feedback Correctif Sécu<br/><i>Modèle : Sonnet</i>]
    FbSecuComp --> DevComp
    JevSecuComp -->|Validé| DocComp[Doc & Commit Git<br/><i>Modèle : Haiku</i>]

    DocSimple --> End([Terminé : Artefacts Sauvegardés dans output/])
    DocMoy --> End
    DocComp --> End
```

---

## 📊 Matrice des Rôles et Modèles

| Étape / Rôle | Tâche Simple | Tâche Moyenne | Tâche Complexe |
| :--- | :--- | :--- | :--- |
| **1. Spécification Technique** | Sonnet | Sonnet | Sonnet |
| **2. Routage & Spécialité Dev** | Jev (`choice`) | Jev (`choice`) | Jev (`choice`) |
| **3. Agent de Développement** | **Sonnet** | **Sonnet** | **Opus** |
| **4. Check Bug / Qualité** | **Sonnet** | **Sonnet** | **Opus** |
| **5. Décision Qualité** | Jev (`noul`/`binary`) | Jev (`noul`/`binary`) | Jev (`noul`/`binary`) |
| **6. Feedback Qualité (si rejet)** | **Haiku** | **Haiku** | **Sonnet** |
| **7. Check Sécurité** | *(Non exécuté)* | **Sonnet** | **Sonnet** |
| **8. Décision Sécurité** | *(Non exécuté)* | Jev (`noul`/`binary`) | Jev (`noul`/`binary`) |
| **9. Feedback Sécurité (si rejet)**| *(Non exécuté)* | **Haiku** | **Sonnet** |
| **10. Documentation & Commit Git**| **Sonnet** | **Haiku** | **Haiku** |

---

## 📁 Structure du Projet

```text
.
├── cli.py                     # Point d'entrée en ligne de commande (CLI interactif, options, gestion UTF-8)
├── orchestrator.py            # Moteur de machine à états finis, isolation des contextes et persistance disque
├── models.py                  # Modèles de données typés (WorkflowType, DevSpecialty, StepRecord, Report)
├── config.py                  # Configuration globale, détection multiplateforme de Claude et parser .env
├── clients/
│   ├── claude_cli.py          # Wrapper subprocess pour le CLI Claude Code headless (-p, cross-platform)
│   └── jev_client.py          # Client API HTTP TypeSafe Jev (System One / Decide + mock heuristique)
├── tests/
│   └── test_orchestrator.py   # Suite de tests unitaires (couverture complète des 3 branches et isolation)
├── output/                    # Dossier des artefacts persistés (code produit, documentation, rapport d'audit)
├── sp_cification_proposition_d_architecture_multi_agents.md  # Spécification technique source du projet
├── .env.example               # Modèle des variables d'environnement
├── requirements.txt           # Dépendances Python minimales
└── .gitignore                 # Exclusions Git (fichiers temporaires, caches, venv)
```

---

## 🚀 Installation & Prérequis

### 1. Prérequis Généraux
* **Python 3.10 ou supérieur**
* **Node.js** (requis pour installer le CLI Claude Code)
* Un compte **Claude Max 5x** ou Claude Pro avec session active.
* Une clé API **TypeSafe** pour le modèle Jev (optionnel : un mode simulation/mock heuristique est activé automatiquement si aucune clé n'est fournie).

---

### 2. Guide d'Installation par Système d'Exploitation

#### 🐧 Linux & 🍎 macOS

```bash
# 1. Installer et connecter Claude Code CLI
npm install -g @anthropic-ai/claude-code
claude auth login

# 2. Cloner le dépôt
git clone https://github.com/GirardotN/Workflow-Claude-Code.git
cd Workflow-Claude-Code

# 3. Créer et activer l'environnement virtuel
python3 -m venv venv
source venv/bin/activate

# 4. Installer les dépendances
pip install -r requirements.txt
```

#### 🪟 Windows (PowerShell ou Invite de commandes CMD)

```cmd
:: 1. Installer et connecter Claude Code CLI
npm install -g @anthropic-ai/claude-code
claude auth login

:: 2. Cloner le dépôt
git clone https://github.com/GirardotN/Workflow-Claude-Code.git
cd Workflow-Claude-Code

:: 3. Créer et activer l'environnement virtuel
python -m venv venv
venv\Scripts\activate

:: 4. Installer les dépendances
pip install -r requirements.txt
```

> **Note Windows :** Le CLI `claude` est automatiquement résolu sous Windows sous la forme `claude.cmd`. L'orchestrateur configure automatiquement l'interpréteur et la console en encodage UTF-8.

---

## ⚙️ Configuration (`.env`)

Copiez le modèle [.env.example](.env.example) vers `.env` :

```bash
cp .env.example .env     # Linux / macOS
copy .env.example .env   # Windows
```

Ajustez les variables selon vos besoins :

| Variable | Description | Valeur par défaut |
| :--- | :--- | :--- |
| `TYPESAFE_API_KEY` | Clé API pour TypeSafe Jev (obtenue sur [typesafe.ai](https://typesafe.ai)) | `""` *(active le mock heuristique si vide)* |
| `TYPESAFE_API_URL` | Endpoint officiel TypeSafe System One | `https://api.typesafe.ai/v1/systemone` |
| `TYPESAFE_FALLBACK_URL` | Endpoint de secours TypeSafe Decide direct | `https://api.typesafe.ai/v1/decide` |
| `CLAUDE_BIN` | Chemin absolu personnalisé vers le binaire `claude` | Détection automatique (`PATH`, AppData, npm) |
| `CLAUDE_TIMEOUT_SECONDS` | Délai d'expiration maximum par appel au CLI Claude | `180` |
| `MAX_WORKFLOW_RETRIES` | Nombre maximum de cycles de feedback (Circuit Breaker) | `4` |
| `MOCK_SERVICES` | Activer la simulation intégrale sans appel réseau/CLI (`1` ou `0`) | `0` |

---

## 💻 Guide d'Utilisation

### 1. Mode In-Repo sur un Projet Existant (Recommandé)

Déployez l'orchestrateur directement sur une base de code existante comportant des dizaines de fichiers. L'agent explore l'arborescence, localise les fonctions ou composants cibles, applique les modifications chirurgicales in-situ et valide le résultat sur le **`git diff`** réel avec rollback automatique en cas de rejet :

```bash
# Explorer le projet, modifier le fichier cible et afficher le git diff validé :
python3 cli.py --project-dir /chemin/vers/mon-projet "Modifie la fonction de tri dans l'onglet x pour trier par date décroissante"

# Option --commit : Appliquer automatiquement le commit conventionnel dans l'historique Git :
python3 cli.py --project-dir /chemin/vers/mon-projet --commit "Modifie la fonction de tri dans l'onglet x pour trier par date décroissante"
```

### 2. Mode Standalone (Génération d'un fichier neuf)

Pour générer un module autonome sans intervenir sur un projet existant :

```bash
python3 cli.py --standalone "Créer un service FastAPI d'authentification JWT avec rate limiting Redis"
```

### 3. Mode Simulation / Hors Ligne (`--mock`)

Le mode `--mock` permet de tester le flux de la machine à états, le routage et les modifications in-situ localement, de façon instantanée, sans dépendre du réseau ni consommer de session CLI :

```bash
python3 cli.py --mock --project-dir /chemin/vers/mon-projet "Modifie la fonction de tri dans l'onglet x"
```

### 4. Options de la Ligne de Commande

```text
usage: cli.py [-h] [--mock] [--max-retries MAX_RETRIES] [--project-dir PROJECT_DIR]
              [--standalone] [--commit] [--workspace WORKSPACE] [-v] [prompt]

Arguments positionnels :
  prompt                 Prompt simple décrivant la tâche de développement ou de refactoring

Options :
  -h, --help             Affiche l'aide et quitte
  --mock                 Exécute le workflow en mode simulation
  --max-retries N        Nombre maximum d'allers-retours de feedback (défaut : 4)
  --project-dir DOSSIER  Répertoire du projet existant à modifier (active le mode In-Repo si sous Git)
  --standalone           Forcer le mode autonome (génération d'un fichier dans output/ au lieu d'éditer le projet)
  --commit               Créer automatiquement le commit Git conventionnel si les modifications sont validées
  --workspace DOSSIER    Répertoire où persister le rapport d'audit et la documentation (défaut : ./output)
  -v, --verbose          Active la journalisation détaillée (DEBUG)
```

---

## 📂 Artefacts Générés

À la fin de chaque exécution validée, les fichiers suivants sont persistés dans le dossier spécifié (`./output` par défaut) :

1. **`LATEST_PATCH.diff`** *(Mode In-Repo)* : Le patch Git exact appliqué aux fichiers du projet.
2. **`generated_solution.<ext>`** *(Mode Standalone)* : Le code source complet produit (extension automatique : `.py`, `.ts`, `.tsx`, `.cs`).
3. **`GENERATED_DOC.md`** : La documentation technique d'utilisation accompagnée de la proposition de message de commit Git au format *Conventional Commits*.
4. **`WORKFLOW_AUDIT.md`** : Le journal d'audit complet retraçant chaque étape chronométrée, les modèles assignés, les fichiers modifiés et les validations accordées par Jev.


---

## 🧪 Tests Unitaires

Une suite complète de tests vérifie de manière déterministe les comportements clés :
* **Branche Simple :** Modèles Sonnet, absence d'exécution de l'audit sécurité, finalisation immédiate.
* **Branche Moyenne :** Cycle Dev $\rightarrow$ Qualité $\rightarrow$ Sécurité, simulation d'un rejet sécurité et prise en compte du feedback Haiku.
* **Branche Complexe :** Assignation automatique des modèles **Opus** pour le Dev et la Qualité, gestion des rejets et feedbacks Sonnet.
* **Isolation des contextes :** Vérification que le relecteur qualité ne reçoit que le code brut, et que le relecteur sécurité reçoit le code plus la review qualité.
* **Circuit Breaker :** Arrêt propre de l'orchestrateur lorsque la limite d'itérations est atteinte, évitant toute boucle infinie.

Pour lancer les tests :

```bash
# Linux / macOS
python3 -m unittest discover -s tests

# Windows
python -m unittest discover -s tests
```

Résultat attendu :
```text
Ran 5 tests in 0.002s

OK
```

---

## 🛡️ Sécurité & Bonnes Pratiques

* **Confidentialité locale :** Le CLI Claude Code s'exécute localement sur votre poste au sein de votre session active.
* **Séparation stricte des privilèges :** Les agents de test et d'audit n'ont pas accès aux prompts de spécification ou aux instructions internes du développeur.
* **Typage et maintenabilité :** Le projet utilise un typage Python rigoureux (`dataclasses`, `Enum`, annotations de types) facilitant son intégration dans des pipelines CI/CD.

---

## 📄 Licence

Ce projet est distribué sous licence MIT. Consultez le fichier `LICENSE` pour plus de détails.
