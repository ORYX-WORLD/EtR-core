# Contribuer à EtR

## Flux de travail

1. Créer ou sélectionner une issue GitHub avec un besoin, des critères
   d'acceptation, un niveau de risque et une méthode de validation.
2. Créer une branche courte : `feat/123-description`, `fix/123-description`,
   `docs/123-description` ou `chore/123-description`.
3. Produire des commits atomiques au format
   [Conventional Commits](https://www.conventionalcommits.org/fr/).
4. Mettre à jour `CHANGELOG.md` dans `[Unreleased]` pour tout changement visible,
   opérationnel ou incompatible.
5. Ouvrir une pull request liée à l'issue (`Closes #123`) et remplir la liste de
   vérification.
6. Faire relire, laisser la CI réussir, puis fusionner sans contourner les
   protections de branche.

## Versionnement

La version canonique se trouve dans `VERSION` et doit correspondre à la version
de `gateway/package.json`.

- `MAJOR` : rupture de compatibilité ou migration obligatoire ;
- `MINOR` : fonctionnalité compatible ;
- `PATCH` : correctif compatible.

Une livraison est identifiée par un tag signé ou protégé `vMAJOR.MINOR.PATCH`.
Le tag, le commit, le manifeste de déploiement et les résultats de validation
doivent permettre de reconstruire la chaîne de traçabilité.

## Exigences minimales

- Aucun secret, jeton, mot de passe, clé privée ou donnée personnelle dans Git,
  les journaux, captures ou manifestes.
- Les changements d'architecture significatifs font l'objet d'un ADR.
- Les incidents de production sont inscrits dans `docs/incidents/`.
- Les scripts shell passent `bash -n`; Python passe `py_compile`; JavaScript
  passe `node --check`.
- Tout déploiement doit produire un manifeste et une preuve de validation.

La définition complète de « terminé » est dans
[`docs/definition-of-done.md`](docs/definition-of-done.md).
