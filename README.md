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
| Fabrique microSD | `etr-sd-factory.service` | bureau Linux local | `src/deploy/raspi/etr_sd_factory.py` |
| Premier démarrage usine | `etr-factory-firstboot.service` | HTTPS sortant | `src/deploy/raspi/etr_factory_firstboot.py` |
| VNC EtR | `etr-vnc.service` | `127.0.0.1:5901` | `src/deploy/raspi/` |
| Relais écran | `etr-remote-screen.service` | WSS sortant | `src/remote_screen_agent.py` |
| Passerelle Cloud | Cloud Run | HTTPS/WSS | `gateway/` |

Aucun service HTTP ou VNC métier n'est volontairement exposé sur `0.0.0.0`. L'accès distant passe par des connexions sortantes authentifiées.

## Première mise en service autonome

Un EtR neuf génère localement sa clé Ed25519 puis demande automatiquement un code temporaire à la passerelle Cloud. Le code Crockford Base32 de 100 bits est affiché sur le dashboard tactile pendant 24 heures. Le client se connecte à l'espace EtR avec une adresse e-mail vérifiée, saisit le numéro de série et ce code, puis le Raspberry échange le code une seule fois contre une session Firebase technique.

La session appareil est émise sans `signBlob` : la passerelle crée ou renouvelle un compte technique déterministe avec un mot de passe éphémère aléatoire de 384 bits, applique les claims EtR, effectue la connexion Firebase et remet uniquement l'ID token et le refresh token au Raspberry authentifié. Le mot de passe n'est ni retourné ni conservé par EtR.

Le protocole, ses propriétés cryptographiques, ses données et ses preuves de déploiement sont décrits dans [`docs/SECURE_ENROLLMENT.md`](docs/SECURE_ENROLLMENT.md).

## Fabrique de cartes microSD

Le raccourci **Créer une carte EtR** est installé exclusivement sur le bureau Linux local. Il détecte uniquement les disques USB/amovibles et refuse le disque qui exécute EtR. La carte choisie est repartitionnée, reçoit une copie cohérente du système de référence, puis toutes les données uniques du banc sont supprimées : jetons Firebase, clé privée Ed25519, état d'enrôlement, identité machine, clés SSH, profils Chromium et runner GitHub.

Le système logiciel reste identique d'une carte à l'autre, mais chaque carte reçoit un ticket de fabrication aléatoire de 256 bits, stocké uniquement sous forme hachée côté Cloud et consommable une seule fois. Au premier démarrage dans le nouveau Raspberry, la carte génère une nouvelle identité Ed25519 liée au numéro de série matériel, échange le ticket, supprime celui-ci puis reprend le parcours d'activation normal. Le Wi-Fi actif du banc peut être copié sans liaison à l'adresse MAC ; sinon le nouvel EtR démarre par Ethernet ou par son portail Wi-Fi.

## Contrat de télémétrie

L'API ne fabrique pas de valeurs métier. Les drivers d'acquisition écrivent un état normalisé dans `/var/lib/etr-core/telemetry.json`; l'API le valide, l'enrichit avec l'état système puis le rend disponible au dashboard et au bridge Firebase. Le schéma est documenté dans [`docs/TELEMETRY_CONTRACT.md`](docs/TELEMETRY_CONTRACT.md).

## Installation et déploiement

```bash
sudo -u oryx -H bash src/deploy/raspi/setup_etr.sh
```

Le déploiement GitHub Actions sur le runner ARM64 vérifie ensuite :

- les unités systemd, y compris le bridge Firebase sous l'utilisateur `oryx` ;
- les endpoints de santé, le contrat API et `/api/v1/enrollment` ;
- la présence du dashboard, du panneau d'association et de la fabrique microSD versionnés ;
- les permissions `root:oryx 0640` des variables et `oryx:oryx 0600` des états ;
- le portail Wi-Fi tactile et Chromium ;
- l'absence d'exposition des ports 8000, 8080 et 5901 ;
- l'écran distant lorsque sa passerelle est configurée ;
- le statut d'enrôlement dans le rapport physique.

La passerelle Cloud possède un workflow distinct qui exécute les tests Node, construit et démarre l'image Docker exacte, déploie Cloud Run, vérifie les routes d'enrôlement puis prouve l'émission d'une session Firebase appareil via GitHub OIDC sans exposer les jetons.

## Tests et règle anti-oubli

```bash
python -m pip install -r requirements.txt -r dashboard/requirements.txt
python -m unittest discover -s tests -v
cd gateway
npm install --no-audit --no-fund
npm test
```

`tests/test_repository_contract.py` bloque la CI si le dashboard, l'enrôlement, l'émetteur de session Firebase, leurs dépendances, unités systemd, tests, workflows ou preuves sont supprimés. Toute pull request fonctionnelle doit également mettre à jour [`docs/PROJECT_TRACKER.md`](docs/PROJECT_TRACKER.md).

## Secrets et état local

Les secrets ne sont jamais versionnés. Les variables restent dans `/etc/etr-core/firebase-bridge.env`, propriété `root:oryx` avec le mode `0640`. Les jetons, codes temporaires et états d'exécution restent dans `/var/lib/etr-core`, propriété `oryx:oryx` avec le mode `0600`.

Le bridge Firebase fonctionne sous l'utilisateur non privilégié `oryx`. Aucun mot de passe, jeton Firebase, clé WSS, code d'activation, jeton de rotation, ticket usine ou clé privée ne doit être ajouté au dépôt.
