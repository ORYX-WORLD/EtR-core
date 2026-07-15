# Passerelle Firebase EtR

## Objectif

La passerelle envoie les données du Raspberry vers Firebase Realtime Database par
une connexion HTTPS **sortante**. Aucun port de la Freebox n'est ouvert et la
console Flask locale reste privée.

Flux:

1. `src/app.py` expose l'état local sur `http://127.0.0.1:8080/`.
2. `firebase_bridge.py` lit cet état.
3. Le compte technique Firebase écrit uniquement dans
   `installations/etr-core/latest`.
4. Le compte client authentifié lit uniquement les installations qui lui sont
   attribuées.

## Préparation Firebase

Dans le projet Firebase du site ORYX:

1. Activer **Authentication > Sign-in method > E-mail/Mot de passe**.
2. Créer deux utilisateurs:
   - un compte client d'essai;
   - un compte technique distinct pour le Raspberry.
3. Activer **Realtime Database** en région européenne.
4. Publier les règles de `firebase/database.rules.json`.
5. Dans les données, associer les UID:
   - `deviceAccess/UID_DU_RASPBERRY = "etr-core"`
   - `clientAccess/UID_DU_CLIENT/etr-core = true`

Le mot de passe du compte technique reste uniquement dans le Raspberry. Il ne
doit jamais être ajouté à GitHub, au site ou à une capture d'écran.

## Installation sur le Raspberry

Depuis le dépôt cloné sur EtR:

```bash
git pull
sudo bash scripts/install_firebase_bridge.sh
sudo nano /etc/etr-core/firebase-bridge.env
```

Compléter:

```ini
FIREBASE_API_KEY=cle_api_web_du_projet
FIREBASE_AUTH_EMAIL=compte-technique-etr@example.com
FIREBASE_AUTH_PASSWORD=mot-de-passe-du-compte-technique
FIREBASE_DATABASE_URL=https://PROJECT_ID-default-rtdb.europe-west1.firebasedatabase.app
ETR_INSTALLATION_ID=etr-core
ETR_LOCAL_API_URL=http://127.0.0.1:8080/
ETR_BRIDGE_INTERVAL=15
```

Puis:

```bash
sudo systemctl restart etr-firebase-bridge
sudo systemctl status etr-firebase-bridge --no-pager
sudo journalctl -u etr-firebase-bridge -f
```

Le fichier d'environnement est créé avec les droits `0600`.

## Données métier

Le service actuel `src/app.py` retourne le nom du service et la charge CPU.
La passerelle transmet automatiquement toute nouvelle clé JSON ajoutée à cette
API. Les champs reconnus immédiatement par le site sont:

- `pressure_bar`
- `compressor_state` (ou `compressor_on`)
- `alerts_active`
- `updated_at`

Il reste donc à relier l'API locale à la source déjà utilisée par la console
pression/compresseur visible sur l'écran du Raspberry.
