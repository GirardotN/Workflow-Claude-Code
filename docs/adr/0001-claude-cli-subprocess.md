# ADR 0001 — Piloter Claude par le CLI `claude -p` (sous-processus)

- **Statut :** Accepté
- **Date :** 2026-10-01
- **Répond à :** la question posée en §5.1 de la [spécification d'origine](0000-spec-origine.md) (CLI `-p` ou sous-agents natifs `.claude/agents/` + serveur MCP pour Jev ?)

## Contexte

Contraintes non négociables du projet : **aucun crédit d'API Claude** (tout passe par la session de l'abonnement) et un **contrôle déterministe** de la chaîne d'agents (qui a quels outils, quel modèle, quelles données, dans quel ordre).

## Décision

L'orchestrateur est un programme Python qui lance **un sous-processus `claude -p` par appel**, en mode non interactif :
- prompt sur **stdin**, réponse `--output-format json` ;
- modèle (`--model`), outils (`--tools=`, `--allowedTools`, `--disallowedTools`), mode de permission et prompt système **par rôle** ;
- environnement du sous-processus **nettoyé** des variables de facturation à l'usage ;
- `--no-session-persistence` : aucun état conservé entre appels.

## Pourquoi

- **Contrôle total de l'isolation** : un relecteur reçoit `--tools=` (aucun outil) et un répertoire vide ; l'agent de développement reçoit des outils d'édition. Avec des sous-agents natifs, le contexte et les outils seraient gérés par la session parente, avec moins de garanties vérifiables.
- **Orchestration déterministe en dehors du modèle** : la FSM, les boucles de feedback, le circuit breaker et les décisions de Jev ne dépendent pas d'un LLM qui « décide » d'appeler l'étape suivante.
- **Testabilité** : le binaire est remplaçable par un faux (`tests/fake_claude.py`) ; on teste stdin, environnement, erreurs et timeouts sur les trois systèmes d'exploitation sans jamais appeler Claude.
- **Zéro crédit API vérifiable** : on contrôle l'environnement du processus.

## Conséquences

- (+) Rôles, modèles, outils et données sont explicites et testés.
- (−) Chaque appel relance le CLI (latence de démarrage) et ne partage pas de cache de contexte : les prompts réintègrent ce dont l'agent a besoin (spécification, diff).
- (−) Dépendance au format de sortie du CLI (`is_error`, `result`, `total_cost_usd`…) et à ses options ; `workflow --doctor` vérifie leur présence et le client lit défensivement la sortie.
- (−) Pas d'`--max-turns` dans la version testée (2.1.286) : la durée est bornée par des délais par rôle.
- Un serveur MCP pour Jev n'apporterait rien : Jev est appelé par l'orchestrateur, pas par Claude.

## Alternatives écartées

- **Sous-agents natifs `.claude/agents/` + MCP** : voir ci-dessus (contrôle et testabilité moindres).
- **SDK / API Anthropic** : exclu par la contrainte « zéro crédit API ».
