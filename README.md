# EtR-core

EtR est le logiciel embarqué du Raspberry Pi ORYX. Le dépôt est la source de vérité du produit Edge : API locale, dashboard tactile, parcours Wi-Fi, affichage kiosque, enrôlement sécurisé, publication Firebase et écran distant.

## Architecture exécutable

| Composant | Service | Écoute | Source versionnée |
|---|---|---:|---|
| API locale | `etr.service` | `127.0.0.1:8080` | `src/app.py` |
| Dashboard tactile | `etr-dashboard.service` | `127.0.0.1:8000` | `dashboard/` |
| Bridge Firebase et enrôlement | `etr-firebase-bridge.service` | HTTPS sortant | `src/firebase_bridge.py` |
| Portail Wi-Fi | `etr-wifi-portal.service` | `127.0.0.1:8090` | `src/wifi_portal.py` |
| Bureau SPI | `spi-desktop.service` | écran local | `src/deploy/raspi/` |
| Kiosque Chromium | `etr-kiosk.service` | écran local | `src/deploy/raspi/` |
| VNC EtR | `etr-vnc.service` | `127.0.0.1:5901` | `src/deploy/raspi/` |
| Relais écran | `etr-remote-screen.service` | WSS sortant | `src/remote_screen_agent.py` |
| Passerelle Cloud | Cloud Run | HTTPS/WSS | `gateway/` |

Aucun service HTTP ou VNC métier n'est volontairement exposé sur `0.0.0.0`. L'accès distant passe par des connexions sortantes authentifiées.

## Première mise en service autonome

Un EtR neuf demande automatiquement un code temporaire à la passerelle Cloud. Le code Crockford Base32 de 100 bits est affiché sur le dashboard tactile pendant 24 heures. Le client se connecte à l'espace EtR, saisit le numéro de série et ce code, puis le Raspberry échange le code une seule fois contre une identité Firebase technique.

Le protocole, ses propriétés cryptographiques, ses données et ses preuves de déploiement sont décrits dans [`docs/SECURE_ENROLLMENT.md`](docs/SECURE_ENROLLMENT.md).

## Contrat de télémétrie

L'API ne fabrique pas de valeurs métier. Les drivers d'acquisition écrivent un état normalisé dans `/var/lib/etr-core/telemetry.json`; l'API le valide, l'enrichit avec l'état système puis le rend disponible au dashboard et au bridge Firebase. Le schéma est documenté dans [`docs/TELEMETRY_CONTRACT.md`](docs/TELEMETRY_CONTRACT.md).

## Installation et déploiement

```bash
sudo -u oryx -H bash src/deploy/raspi/setup_etr.sh
```

Le déploiement GitHub Actions sur le runner ARM64 vérifie ensuite :

- les unités systemd, y compris le bridge Firebase sous l'utilisateur `oryx` ;
- les endpoints de santé, le contrat API et `/api/v1/enrollment` ;
- la présence du dashboard et du panneau d'association versionnés ;
- les permissions `root:oryx 0640` des variables et `oryx:oryx 0600` des états ;
- le portail Wi-Fi tactile et Chromium ;
- l'absence d'exposition des ports 8000, 8080 et 5901 ;
- l'écran distant lorsque sa passerelle est configurée ;
- le statut d'enrôlement dans le rapport physique.

La passerelle Cloud possède un workflow distinct qui exécute les tests Node, construit noVNC, déploie Cloud Run et vérifie que `/healthz` annonce `enrollment: "v1"`.

## Tests et règle anti-oubli

```bash
python -m pip install -r requirements.txt -r dashboard/requirements.txt
python -m unittest discover -s tests -v
cd gateway
npm install --no-audit --no-fund
npm test
```

`tests/test_repository_contract.py` bloque la CI si le dashboard, l'enrôlement, leurs dépendances, unités systemd, tests, workflows ou preuves sont supprimés. Toute pull request fonctionnelle doit également mettre à jour [`docs/PROJECT_TRACKER.md`](docs/PROJECT_TRACKER.md).

## Secrets et état local

Les secrets ne sont jamais versionnés. Les variables restent dans `/etc/etr-core/firebase-bridge.env`, propriété `root:oryx` avec le mode `0640`. Les jetons, codes temporaires et états d'exécution restent dans `/var/lib/etr-core`, propriété `oryx:oryx` avec le mode `0600`.

Le bridge Firebase fonctionne sous l'utilisateur non privilégié `oryx`. Aucun mot de passe, jeton Firebase, clé WSS, code d'activation ou jeton de rotation ne doit être ajouté au dépôt.
