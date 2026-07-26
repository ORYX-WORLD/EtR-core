# Suivi technique EtR-core

Dernière mise à jour : 2026-07-26

Ce document est une barrière de gouvernance du dépôt. Toute modification fonctionnelle doit mettre à jour son état ou ajouter une ligne. Le suivi consolidé site + application est maintenu dans le dépôt privé `ORYX-PROJETS`.

| ID | Périmètre | Fonction / risque | Priorité | État | Constat | Correction / preuve | Prochaine action |
|---|---|---|---|---|---|---|---|
| ETR-001 | Dashboard local | Code installé hors GitHub sous `/opt/etr/dashboard` | P0 | Corrigé le 2026-07-26 | Service actif mais sources absentes du dépôt | Dashboard, dépendances, unité systemd et tests versionnés sous `dashboard/` | Valider le rendu sur l'écran physique après déploiement |
| ETR-002 | Déploiement | Le workflow ne surveillait ni `src/app.py` ni le dashboard | P0 | Corrigé le 2026-07-26 | Un changement applicatif pouvait ne pas être déployé | Filtres, contrôles HTTP et rapport de déploiement étendus | Conserver les contrôles comme conditions de réussite |
| ETR-003 | API locale | API limitée à un pourcentage CPU | P0 | Corrigé structurellement le 2026-07-26 | Aucun contrat de données métier stable | API v1, santé, système, télémétrie normalisée et compatibilité Firebase | Raccorder les vrais drivers Modbus/GPIO au fichier de télémétrie |
| ETR-004 | Sécurité Edge | API Flask de développement exposée sur `0.0.0.0` | P0 | Corrigé le 2026-07-26 | Service joignable sur toutes les interfaces | Gunicorn sur loopback + sandbox systemd | Vérifier les ports après chaque déploiement |
| ETR-005 | Wi-Fi tactile | Mise en service sans PC | P0 | Déployé | Test de déploiement 2026-07-19 réussi | Portail, clavier tactile et reprise automatique contrôlés | Test terrain sur WPA2/WPA3 et réseau masqué |
| ETR-006 | Écran SPI / kiosque | Reprise au démarrage à froid | P0 | Déployé | Correctif fusionné le 2026-07-18 | Dépendances systemd et anti-blanking testés | Test après coupure électrique prolongée |
| ETR-007 | Écran distant | VNC local + relais WSS | P0 | Déployé sous condition de configuration | Contrôle de loopback présent dans CI | VNC 5901 local et agent sortant vérifiés | Test E2E depuis un compte client réel |
| ETR-008 | Firebase | Publication de la télémétrie | P0 | Partiel | Bridge fonctionnel, données métier réelles non raccordées | Contrat API v1 disponible | Définir le mapping capteurs et alarmes par type d'installation |
| ETR-009 | Scalabilité | Passerelle distante mono-instance en mémoire | P1 | À faire | Sessions et tickets non partagés entre instances | Architecture documentée | Ajouter Redis/Pub/Sub avant montée en charge |
| ETR-010 | Secrets | Dépôt public et configuration sensible | P0 | Sous contrôle | Secrets hors Git, fichiers locaux en 0600 | Tests et revue de configuration | Activer secret scanning et protection de branche dans GitHub |
| ETR-011 | Fabrication microSD | Préparation manuelle et identité dupliquée entre clients | P0 | Implémenté, test terrain requis | Aucune chaîne de provisionnement n'était versionnée | Script Windows avec garde-fous, clé SSH, identité matérielle et bootstrap réseau versionnés sous `provisioning/` | Fabriquer la carte du Pi 3 de Joffrey et valider le premier démarrage Ethernet |
