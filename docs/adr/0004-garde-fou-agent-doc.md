# ADR 0004 — Agent de documentation encadré par un garde-fou a posteriori

- **Statut :** Accepté
- **Date :** 2026-10-01

## Contexte

L'étape finale doit « créer / mettre à jour la doc » (spécification d'origine), pas seulement produire du texte. Cela suppose un agent doté d'**outils d'édition** dans le dépôt, après la validation du code par la revue et par Jev. Le risque : un agent (erreur, injection de prompt via le contenu du dépôt) modifie du **code déjà validé** ou d'autres fichiers sensibles, hors de toute revue.

## Décision

1. L'agent `DOC_EDIT` reçoit les outils d'édition mais **jamais Bash**, et un prompt qui interdit de toucher au code.
2. Cette consigne n'est **pas** la garantie. La garantie est un contrôle **après coup** (`doc_guard.py`) :
   - un instantané des fichiers déjà modifiés (le code validé) est pris avant l'agent ;
   - ensuite tout fichier qui n'est pas de la documentation (`*.md`, `*.rst`, `*.adoc`, `README`, `CHANGELOG`…) est **annulé** : nouveau fichier supprimé, fichier suivi restauré depuis HEAD, suppression annulée ;
   - un fichier du développeur modifié ou « ramené à HEAD » par l'agent est remis dans l'état de l'instantané.
3. Étape **au mieux** : une erreur de l'agent (timeout, quota) ne remet pas en cause le code validé ; ce qui a été écrit avant l'échec est conservé s'il s'agit de documentation.
4. Désactivable (`--no-doc-edit`, `DOC_EDIT=0`) ; sans objet quand le développeur n'a rien modifié ; mode Standalone : la doc reste un texte dans le dossier de rapports.

## Conséquences

- (+) Le code validé ne peut pas changer après la revue, quoi que fasse l'agent doc (testé, y compris par mutation du garde-fou).
- (+) La documentation est dans le même commit que le code, et son diff est isolé (`DOC_CHANGES.diff`).
- (−) La liste blanche est fondée sur les noms de fichiers : un `docs/conf.py` ou un `.txt` ne sont pas modifiables.
- (−) Le contenu de la documentation modifiée n'est pas revu par Jev (seul le code l'est) : relisez le diff avant de fusionner.

## Alternatives écartées

- **Se fier au prompt seul** : insuffisant face à une erreur ou une injection.
- **Faire valider la doc par Jev** : coût et latence pour un risque limité (la doc n'est pas du code exécuté) ; pourrait être ajouté plus tard.
- **Interdire toute édition** (texte seul) : écart avec la spécification d'origine ; conservé comme option (`--no-doc-edit`).
