# Journal des changements

Les changements notables de ce projet sont consignés ici. Le format suit
[Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et les versions suivent
[Semantic Versioning](https://semver.org/lang/fr/).

## [Unreleased]

### Added

- Socle de traçabilité d'ingénierie : version, registres, ADR, incidents,
  manifestes de déploiement et contrôles CI.
- Désactivation persistante de la veille X11 sur l'écran SPI, avec dépendance
  déclarée et invariant vérifié par la CI.
- Verrouillage reproductible des dépendances npm de la passerelle et mise à
  niveau d'Express, Firebase Admin et ws pour supprimer les vulnérabilités
  directes de sévérité haute.

## [1.0.0] - 2026-07-16

### Added

- Première référence formelle de version du système EtR.
- Portail Wi-Fi tactile, affichage SPI, passerelle Firebase et écran distant.

> Cette version constitue le point de départ du registre formel. L'historique
> Git antérieur reste la source de vérité pour les travaux précédents.

[Unreleased]: https://github.com/ORYX-WORLD/EtR-core/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/ORYX-WORLD/EtR-core/releases/tag/v1.0.0
