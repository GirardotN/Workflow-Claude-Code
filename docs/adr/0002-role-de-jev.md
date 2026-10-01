# ADR 0002 — Rôle de Jev : décider, en fail-closed

- **Statut :** Accepté
- **Date :** 2026-10-01
- **Point ouvert :** faut-il rendre bloquant un `VERDICT: FAIL` de relecteur contredisant Jev ? (voir « Questions »)

## Contexte

Le concept sépare le raisonnement (Claude) de la décision (Jev, service TypeSafe « System One »). Le premier client Jev lisait une clé de réponse qui n'existe pas dans l'API réelle (`results.<clé>.probability` au lieu de `answers.<clé>.noul`) et validait tout par défaut ; l'endpoint de repli `/v1/decide` n'existe pas (404). Les formats ci-dessous ont été relevés contre le service réel.

## Décision

1. **Format** : `POST /v1/systemone` ; routage par question `choice` (`answers.<clé>.choice`, `confidence`) ; validation par question `noul` (`answers.<clé>.noul` ∈ [0, 1] = probabilité de « oui »). Validé si `noul` ≥ seuil (0,5, `JEV_THRESHOLD`).
2. **Fail-closed** : toute réponse absente, hors format, inconnue, ou toute panne (après reprises sur réseau/429/5xx) lève `JevApiError` et **arrête** le workflow ; jamais de validation par défaut. Sans clé TypeSafe, refus de démarrer ; la simulation n'existe que via `--mock`, avec un bandeau permanent.
3. **Jev décide.** Le verdict structuré des relecteurs (`VERDICT: PASS|FAIL`) sert de **contrôle de cohérence** : une contradiction est journalisée et tracée (`report.decisions`, `consistent: false`), la décision de Jev s'applique.
4. **Confidentialité** : le texte envoyé est masqué (secrets) et plafonné (l'API refuse ~32 000 tokens ; plafond prudent de 60 000 caractères, la revue est conservée en priorité) ; `--jev-send review-only` n'envoie aucun code.
5. **Traçabilité** : chaque décision (étape, résultat, probabilité, seuil, latence, tokens, tentatives, troncature) est enregistrée et listée.

## Conséquences

- (+) Un feu vert est toujours une réponse réelle du service ; l'audit permet de comprendre chaque décision.
- (+) Les formats sont couverts par un faux serveur au format réel (`tests/fake_jev.py`).
- (−) Dépendance à un service tiers : indisponibilité = arrêt (voulu), et du code (masqué) quitte la machine sauf `review-only`.
- (−) La probabilité n'est pas calibrée : des valeurs de 0,48 à 0,77 ont été observées sur des revues normales ; le seuil est réglable mais mérite d'être réévalué après des runs réels.

## Questions ouvertes

- Rendre un `VERDICT: FAIL` bloquant (porte « ET » entre relecteur et Jev) augmenterait la sûreté mais transférerait une part de la décision à Claude, à l'opposé du concept ; la décision actuelle est de ne pas le faire.
- Une question `noul` unique par appel : l'API accepte plusieurs questions par requête (routage complexité + spécialité en un appel) ; non exploité pour garder des étapes et des traces séparées.
