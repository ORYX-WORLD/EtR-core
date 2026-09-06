# Architecture EtR — source de vérité

Ce document décrit la chaîne réellement utilisée sur le Raspberry EtR. Une modification n'est validée que si le maillon réel concerné est identifié puis vérifié sur le Raspberry.

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
   - Route de santé : `/api/health`
   - L'accès distant/noVNC est un canal de maintenance, pas le cœur fonctionnel de la Fabrique.

7. **Runner GitHub Raspberry**
   - Répertoire attendu : `/home/oryx/actions-runner`
   - Processus attendu : `Runner.Listener` / `runsvc.sh`
   - Labels requis par le workflow physique : `self-hosted`, `Linux`, `ARM64`
   - Le runner est un moyen de déploiement. Son état ne constitue jamais, à lui seul, une preuve fonctionnelle de la Fabrique.

8. **Workflow de déploiement physique**
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
- **Interface graphique** : fenêtre réellement mappée sur le DISPLAY du bureau ; idéalement capture visuelle pendant la validation.
- **Réseau** : résolution DNS depuis le Raspberry + réponse HTTP de la gateway.
- **Runner** : service/processus actif + workflow ARM64 effectivement pris en charge.
- **Fabrique** : lancement depuis le raccourci réel et comportement visible attendu, sans déclencher d'écriture destructive pendant un simple diagnostic.
