# EtR-core

EtR est le logiciel embarqué du Raspberry Pi ORYX. Le dépôt est la source de vérité du produit Edge : API locale, dashboard tactile, parcours Wi-Fi, affichage kiosque, publication Firebase et écran distant.

## Architecture exécutable

| Composant | Service | Écoute | Source versionnée |
|---|---|---:|---|
| API locale | `etr.service` | `127.0.0.1:8080` | `src/app.py` |
| Dashboard tactile | `etr-dashboard.service` | `127.0.0.1:8000` | `dashboard/` |
| Portail Wi-Fi | `etr-wifi-portal.service` | `127.0.0.1:8090` | `src/wifi_portal.py` |
| Bureau SPI | `spi-desktop.service` | écran local | `src/deploy/raspi/` |
| Kiosque Chromium | `etr-kiosk.service` | écran local | `src/deploy/raspi/` |
| VNC EtR | `etr-vnc.service` | `127.0.0.1:5901` | `src/deploy/raspi/` |
| Relais écran | `etr-remote-screen.service` | WSS sortant | `src/remote_screen_agent.py` |
| Télémétrie cloud | bridge Firebase | HTTPS sortant | `src/firebase_bridge.py` |

Aucun service HTTP ou VNC métier n'est volontairement exposé sur `0.0.0.0`. L'accès distant passe par des connexions sortantes authentifiées.

## Contrat de télémétrie

L'API ne fabrique pas de valeurs métier. Les drivers d'acquisition écrivent un état normalisé dans `/var/lib/etr-core/telemetry.json`; l'API le valide, l'enrichit avec l'état système puis le rend disponible au dashboard et au bridge Firebase. Le schéma est documenté dans [`docs/TELEMETRY_CONTRACT.md`](docs/TELEMETRY_CONTRACT.md).

## Installation et déploiement

```bash
sudo -u oryx -H bash src/deploy/raspi/setup_etr.sh
```

Le déploiement GitHub Actions sur le runner ARM64 vérifie ensuite :

- les unités systemd ;
- les endpoints de santé et le contrat API ;
- la présence du dashboard versionné ;
- le portail Wi-Fi tactile et Chromium ;
- l'absence d'exposition des ports 8000, 8080 et 5901 ;
- l'écran distant lorsque sa passerelle est configurée.

## Tests et règle anti-oubli

```bash
python -m pip install -r requirements.txt -r dashboard/requirements.txt
python -m unittest discover -s tests -v
```

`tests/test_repository_contract.py` bloque la CI si le dashboard, ses dépendances, son unité systemd, ses tests, son branchement dans l'installateur ou le suivi projet sont supprimés. Toute pull request fonctionnelle doit également mettre à jour [`docs/PROJECT_TRACKER.md`](docs/PROJECT_TRACKER.md).

## Secrets et état local

Les secrets ne sont jamais versionnés. Ils restent dans `/etc/etr-core` avec des permissions restrictives ; les jetons et états d'exécution restent dans `/var/lib/etr-core`. Aucun mot de passe, jeton Firebase, clé WSS ou code d'activation ne doit être ajouté au dépôt.
