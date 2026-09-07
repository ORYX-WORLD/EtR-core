# Architecture EtR — source de vérité

Ce document décrit la chaîne réellement utilisée sur le Raspberry EtR. Une modification n'est validée que si le maillon réel concerné est identifié puis vérifié sur le Raspberry.

## Identité matérielle et système

- Hôte : `etr-core`
- Utilisateur applicatif : `oryx`
- Architecture : `aarch64` / ARM64
- Noyau validé : `6.18.39+rpt-rpi-v8`
- Dépôt installé : `/home/oryx/EtR-core`
- Écran SPI physique : framebuffer `fb_ili9486` (index `/dev/fb0` ou `/dev/fb1`), `480x320`, 16 bits

## Topologie graphique

La présence de deux bureaux est volontaire. Ils ont deux fonctions distinctes et ne doivent pas être confondus.

1. **Bureau physique SPI**
   - DISPLAY : `:1`
   - Résolution : `480x320`
   - Service : `spi-desktop.service`
   - Démarrage : `/usr/local/bin/start_spi_desktop.sh`
   - Xorg sur `vt2`, puis une seule session LXDE sous `oryx`.

2. **Bureau distant haute résolution**
   - DISPLAY : `:2`
   - Résolution : `1280x720x24`
   - Service : `etr-remote-desktop.service`
   - Démarrage : `/usr/local/bin/start_remote_desktop.sh`
   - Xvfb puis une seule session LXDE sous `oryx`.
   - `etr-vnc.service` expose uniquement ce DISPLAY `:2` sur `127.0.0.1:5901`.
   - `etr-remote-screen.service` relaie ce VNC local vers la gateway EtR.

Contrat attendu : exactement une session LXDE sur `:1` et une session LXDE sur `:2`. Deux sessions LXDE sur le même DISPLAY constituent une dérive.

## Chaîne principale Fabrique microSD

1. **Bureau Linux**
   - Fichier installé : `/home/oryx/Desktop/SD-V1.1.desktop`
   - Nom attendu : `SD V1.1`
   - Source dépôt : `src/deploy/raspi/etr-sd-factory.desktop`
   - Commande attendue : `sudo -n /usr/local/bin/etr-sd-factory-launch.sh`
   - Anciens noms supprimés : `Creer-une-carte-EtR.desktop` et `etr-sd-factory.desktop`

2. **Launcher système**
   - Fichier installé : `/usr/local/bin/etr-sd-factory-launch.sh`
   - Source dépôt : `src/deploy/raspi/etr-sd-factory-launch.sh`
   - Rôle : préparer les dépendances réseau/worker puis redémarrer `etr-sd-factory.service`.

3. **Service Fabrique**
   - Unité : `/etc/systemd/system/etr-sd-factory.service`
   - Source dépôt : `src/deploy/raspi/etr-sd-factory.service`
   - `DISPLAY=:1`
   - `XAUTHORITY=/home/oryx/.Xauthority`
   - Exécution : `.venv/bin/python .../etr_sd_factory_resilient.py`

4. **Application Fabrique**
   - Entrée résiliente : `src/deploy/raspi/etr_sd_factory_resilient.py`
   - Moteur : `src/deploy/raspi/etr_sd_factory_core.py`
   - Interface : `src/deploy/raspi/etr_sd_factory_fast.py`

5. **Réseau**
   - Script : `/usr/local/bin/etr-network-resilience.sh`
   - Services : `etr-network-resilience.service` + `etr-network-resilience.timer`
   - DNS cible : `etr-remote-gateway-7n72m5gopq-ew.a.run.app`

6. **Gateway EtR**
   - Origine : `https://etr-remote-gateway-7n72m5gopq-ew.a.run.app`
   - Routes de santé : `/healthz` et `/api/health`
   - L'accès distant/noVNC est un canal de maintenance, pas le cœur fonctionnel de la Fabrique.

7. **Runner GitHub Raspberry**
   - Racine réelle : `/home/oryx/actions-runner/actions-runner`
   - Binaire : `/home/oryx/actions-runner/actions-runner/bin/Runner.Listener`
   - Service : `actions.runner.ORYX-WORLD-EtR-core.etr-core.service`
   - Processus attendu : `Runner.Listener` / `runsvc.sh`
   - Labels requis par le workflow physique : `self-hosted`, `Linux`, `ARM64`
   - Le runner est un moyen de déploiement. Son état ne constitue jamais, à lui seul, une preuve fonctionnelle de la Fabrique.

8. **Configuration déclarative**
   - Inventaire : `infra/ansible/inventory.yml`
   - Contrat : `infra/ansible/group_vars/all.yml`
   - Playbook : `infra/ansible/site.yml`
   - Contrôle non destructif : `.github/workflows/etr-ansible-check.yml`
   - Le mode `--check --diff` doit précéder toute convergence volontaire.

9. **Workflow de déploiement physique**
   - `.github/workflows/etr-deploy.yml`
   - Runner : `[self-hosted, Linux, ARM64]`
   - Un workflow vert n'est pas une validation finale : la preuve finale doit provenir du Raspberry réel.

## Règle de validation

Ordre obligatoire :

`architecture connue -> diagnostic -> premier maillon défaillant -> correction ciblée -> preuve Raspberry -> test de non-régression -> capitalisation`

Ne jamais modifier plusieurs maillons pour masquer une cause inconnue.

## Preuves minimales par type de changement

- **Raccourci bureau** : fichier installé + `Name=` + `Exec=` vérifiés sur le Raspberry.
- **Service** : unité réellement installée + `systemctl show` conforme + processus attendu lorsqu'il doit être actif.
- **Interface graphique physique** : une seule session LXDE sur `:1`, fenêtre réellement mappée, idéalement preuve visuelle.
- **Bureau distant** : une seule session LXDE sur `:2`, résolution `1280x720`, VNC local sur `:2`, gateway connectée.
- **Réseau** : résolution DNS depuis le Raspberry + réponse HTTP de la gateway.
- **Runner** : service/processus actif + workflow ARM64 effectivement pris en charge.
- **Fabrique** : lancement depuis le raccourci réel et comportement visible attendu, sans déclencher d'écriture destructive pendant un simple diagnostic.
