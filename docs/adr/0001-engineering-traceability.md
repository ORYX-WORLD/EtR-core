# ADR-0001 — Traçabilité du cycle de développement

- État : acceptée
- Date : 2026-07-16
- Décideur : ORYX-WORLD

## Contexte

EtR combine logiciel web, passerelle distante, services système et matériel Raspberry.
Un même incident peut donc traverser plusieurs environnements et déploiements.

## Décision

Adopter SemVer, Conventional Commits, Keep a Changelog et une chaîne de preuve :
issue → branche → pull request → commit → version → manifeste de déploiement →
installation → validation. `main` est protégée et les changements passent par revue.

## Conséquences

Chaque livraison est identifiable et auditable. La discipline ajoute un coût léger de
documentation et de revue, compensé par des diagnostics et retours arrière plus sûrs.
