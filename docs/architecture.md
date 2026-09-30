# Architecture & Modèle Mental

Ce document détaille l'architecture interne, le fonctionnement de la Machine à États Finis (FSM) et les choix de conception qui sous-tendent l'orchestrateur **`Workflow-Claude-Code`**.

---

## Modèle Mental à Deux Niveaux (Système 1 / Système 2)

L'architecture s'inspire de la théorie cognitive des processus duaux :

1. **Système Deux (Cognition Profonde, Raisonnement & Synthèse)** :
   - Délégué aux modèles d'Anthropic (**Claude Opus, Sonnet, Haiku**) via le CLI officiel `claude -p` en mode headless.
   - Responsable de la compréhension sémantique du codebase, de la rédaction des spécifications, de l'écriture chirurgicale du code source et des revues critiques.
   - **Économie de tokens** : Aucune clé `ANTHROPIC_API_KEY` n'est requise ; le système s'appuie sur la session locale active de l'utilisateur (Claude Pro / Max 5x).

2. **Système Un (Décision Binaire Réflexe & Routage Déterministe)** :
   - Délégué au moteur décisionnel **TypeSafe Jev** (endpoints `/v1/systemone` et `/v1/decide`).
   - Responsable des arbitrages rapides : choix du niveau de complexité, sélection de la spécialité technique du développeur, et validation/rejet binaire (Qualité et Sécurité).
   - **Avantage** : Latence ultra-faible (< 200 ms), format déterministe (`choice` ou probabilité binaire `noul`/`binary`), et zéro hallucination de complaisance.

---

## Machine à États Finis (FSM) : Flux des 3 Branches

L'orchestrateur évalue dynamiquement le niveau de complexité de la tâche après la phase de spécification initiale et aiguille l'exécution vers l'une des trois branches :

```mermaid
flowchart TD
    Start(["Invite Utilisateur Initiale"]) --> Spec["Étape 1 : Spécification Technique In-Situ<br/><i>Modèle : Claude Sonnet</i>"]
    Spec --> JevRoute{"Étape 2 : Aiguillage Jev<br/><i>Classification Complexité & Spécialité</i>"}

    %% Branche Simple
    JevRoute -->|"Tâche Simple"| DevSimple["Dev Simple<br/><i>Modèle : Sonnet</i>"]
    DevSimple --> TestsSimple{"Oracle Tests Propre ?"}
    TestsSimple -->|"Échec (Régression)"| FbTestSimple["Feedback d'Erreur Tests"]
    FbTestSimple --> DevSimple
    TestsSimple -->|"Succès"| QualSimple["Check Bug & Qualité<br/><i>Modèle : Sonnet</i>"]
    QualSimple --> JevQualSimple{"Jev : Qualité Validée ?"}
    JevQualSimple -->|"Rejet (Rollback Git)"| FbQualSimple["Feedback Correctif Qualité<br/><i>Modèle : Haiku</i>"]
    FbQualSimple --> DevSimple
    JevQualSimple -->|"Validé"| DocSimple["Doc & Commit Git<br/><i>Modèle : Sonnet</i>"]

    %% Branche Moyenne
    JevRoute -->|"Tâche Moyenne"| DevMoy["Dev Moyen<br/><i>Modèle : Sonnet</i>"]
    DevMoy --> TestsMoy{"Oracle Tests Propre ?"}
    TestsMoy -->|"Échec (Régression)"| FbTestMoy["Feedback d'Erreur Tests"]
    FbTestMoy --> DevMoy
    TestsMoy -->|"Succès"| QualMoy["Check Bug & Qualité<br/><i>Modèle : Sonnet</i>"]
    QualMoy --> JevQualMoy{"Jev : Qualité Validée ?"}
    JevQualMoy -->|"Rejet (Rollback Git)"| FbQualMoy["Feedback Correctif Qualité<br/><i>Modèle : Haiku</i>"]
    FbQualMoy --> DevMoy
    JevQualMoy -->|"Validé"| SecuMoy["Check Cyber-Sécurité<br/><i>Modèle : Sonnet</i>"]
    SecuMoy --> JevSecuMoy{"Jev : Sécurité Validée ?"}
    JevSecuMoy -->|"Rejet (Rollback Git)"| FbSecuMoy["Feedback Correctif Sécu<br/><i>Modèle : Haiku</i>"]
    FbSecuMoy --> DevMoy
    JevSecuMoy -->|"Validé"| DocMoy["Doc & Commit Git<br/><i>Modèle : Haiku</i>"]

    %% Branche Complexe
    JevRoute -->|"Tâche Complexe"| DevComp["Dev Complexe<br/><i>Modèle : Opus</i>"]
    DevComp --> TestsComp{"Oracle Tests Propre ?"}
    TestsComp -->|"Échec (Régression)"| FbTestComp["Feedback d'Erreur Tests"]
    FbTestComp --> DevComp
    TestsComp -->|"Succès"| QualComp["Check Bug & Qualité<br/><i>Modèle : Opus</i>"]
    QualComp --> JevQualComp{"Jev : Qualité Validée ?"}
    JevQualComp -->|"Rejet (Rollback Git)"| FbQualComp["Feedback Correctif Qualité<br/><i>Modèle : Sonnet</i>"]
    FbQualComp --> DevComp
    JevQualComp -->|"Validé"| SecuComp["Check Cyber-Sécurité<br/><i>Modèle : Sonnet</i>"]
    SecuComp --> JevSecuComp{"Jev : Sécurité Validée ?"}
    JevSecuComp -->|"Rejet (Rollback Git)"| FbSecuComp["Feedback Correctif Sécu<br/><i>Modèle : Sonnet</i>"]
    FbSecuComp --> DevComp
    JevSecuComp -->|"Validé"| DocComp["Doc & Commit Git<br/><i>Modèle : Haiku</i>"]

    DocSimple --> OutputSuccess(["Succès : Commit, Merge & Artefacts ./output"])
    DocMoy --> OutputSuccess
    DocComp --> OutputSuccess
```

---

## Isolation Transactionnelle Git & Stash Guard

Pour éliminer tout risque de collision avec les modifications locales de l'utilisateur ou de corruption de branche :

```mermaid
sequenceDiagram
    autonumber
    actor Dev as Développeur
    participant WT as Copie de Travail (Working Tree)
    participant SG as Stash Guard (GitClient)
    participant FSM as Orchestrateur (FSM)
    participant Claude as Claude Code CLI
    participant Tests as Test Runner (Oracle)
    participant Jev as Moteur TypeSafe Jev

    Dev->>FSM: Exécute workflow --project-dir .
    FSM->>SG: Vérifie l'état de l'arbre de travail
    alt Modifications locales non commitées détectées
        SG->>WT: git stash push -u -m "workflow-auto-stash"
        Note over SG,WT: Données utilisateur mises en sécurité
    end

    FSM->>SG: git checkout -b workflow/ai-<timestamp>
    Note over FSM,WT: Branche d'isolation active

    FSM->>Tests: Cycle 0 : Diagnostic de santé initial (Baseline)
    Tests-->>FSM: Snapshot de l'état des tests existants

    rect rgb(240, 245, 255)
        Note over FSM,Jev: Boucle d'Itération & Auto-Réparation
        FSM->>Claude: Dev In-Situ (Outils : Read, Edit, Write, Grep)
        Claude->>WT: Modifications chirurgicales appliquées
        FSM->>Tests: Exécution des tests du projet
        alt Tests en échec (Régression)
            Tests-->>FSM: Trace d'erreur
            FSM->>Claude: Feedback correctif d'erreur
        else Tests au vert
            FSM->>SG: git diff HEAD (avec intent-to-add -N)
            SG-->>FSM: Capture du git diff réel
            FSM->>Claude: Audit Qualité / Sécurité sur le diff
            Claude-->>FSM: Rapport de review
            FSM->>Jev: Validation déterministe (validate)
            alt Rejet Jev
                Jev-->>FSM: Rejet binaire
                FSM->>SG: git rollback (git reset --hard HEAD + git clean -fd)
                Note over FSM,WT: Code défaillant supprimé sans risque
            else Validation Jev
                Jev-->>FSM: Accord de conformité
            end
        end
    end

    FSM->>SG: git commit sur la branche workflow/ai-* (systématique)
    FSM->>Dev: Décision de fusion (--merge, invite interactive ou refus)
    FSM->>SG: Retour sur la branche d'origine (+ git merge --no-ff si accepté)

    Note over FSM,WT: Sortie garantie (succès, échec, exception, Ctrl-C) par IsolatedRun :<br/>rollback si non validé, retour sur la branche d'origine, PUIS stash pop
    opt Stash initialement créé
        FSM->>SG: git stash pop
        SG->>WT: Restauration des fichiers modifiés de l'utilisateur
        Note over Dev,WT: Travail en cours restauré à 100 %
    end
```

---

## Matrice d'Allocation des Modèles

L'orchestrateur adapte la puissance du modèle à l'effort cognitif de chaque phase :

| Étape / Rôle | Tâche Simple | Tâche Moyenne | Tâche Complexe | Justification Cognitive & Économique |
| :--- | :--- | :--- | :--- | :--- |
| **1. Spécification Technique** | **Sonnet** | **Sonnet** | **Sonnet** | Vision architecturale équilibrée et cartographie du codebase. |
| **2. Aiguillage & Rôle Dev** | **Jev** (`choice`) | **Jev** (`choice`) | **Jev** (`choice`) | Déterministe, instantané (< 200 ms), zéro coût de token. |
| **3. Agent de Développement** | **Sonnet** | **Sonnet** | **Opus** | Sonnet pour les tâches directes ; Opus pour les tâches complexes. |
| **4. Oracle de Test** | `TestRunner` | `TestRunner` | `TestRunner` | Exécution native hermétique (`pytest`, `npm`, `cargo`, `go`). |
| **5. Audit Bug & Qualité** | **Sonnet** | **Sonnet** | **Opus** | Rigueur critique ; Opus est intransigeant sur les architectures lourdes. |
| **6. Décision Qualité** | **Jev** (`noul`) | **Jev** (`noul`) | **Jev** (`noul`) | Arbitrage binaire probabiliste impartial. |
| **7. Synthèse Feedback Qualité** | **Haiku** | **Haiku** | **Sonnet** | Puces d'action concises sans surcharge de contexte. |
| **8. Audit Cyber-Sécurité** | *(Non exécuté)* | **Sonnet** | **Sonnet** | Analyse OWASP Top 10, injections, gestion des secrets. |
| **9. Décision Sécurité** | *(Non exécuté)* | **Jev** (`noul`) | **Jev** (`noul`) | Tolérance zéro aux failles logiques ou d'injection. |
| **10. Synthèse Feedback Sécu** | *(Non exécuté)* | **Haiku** | **Sonnet** | Directives correctives impératives. |
| **11. Documentation & Commit** | **Sonnet** | **Haiku** | **Haiku** | Formatage sémantique et commit conventionnel standardisé. |

---

## Isolation Cognitive Stricte

Pour prévenir les biais de confirmation et l'explosion de la fenêtre de contexte :
- **L'Agent Qualité** ne reçoit **que** le code brut ou le `git diff`. Il n'a aucun accès au prompt de spécification initial ni à l'identité du développeur, ce qui garantit une relecture neutre et impartiale.
- **L'Agent Sécurité** reçoit **le code source ET la review qualité** préalable pour identifier les vecteurs d'attaque potentiels.
- **Les Retours Correctifs** sont reformulés sous forme de listes à puces concises sans réinjecter l'intégralité des échanges passés.
