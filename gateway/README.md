# Passerelle d’écran distant EtR

Cette passerelle relaie l’écran VNC d’un Raspberry EtR vers l’espace client ORYX sans ouvrir de port entrant sur le site du client.

## Sécurité

- le Raspberry ouvre une connexion WSS sortante ;
- le navigateur fournit un jeton Firebase Authentication ;
- l’accès est vérifié dans `memberships/{uid}/{installationId}` ;
- l’équipement est vérifié dans `deviceAccess/{uid}` ;
- les tickets d’écran sont à usage unique et expirent après 60 secondes ;
- le port VNC local reste lié à `127.0.0.1`.

## Variables Cloud Run

- `ALLOWED_ORIGINS=https://oryx-froid-industriel.web.app`
- `FIREBASE_DATABASE_URL=https://oryx-froid-industriel-default-rtdb.europe-west1.firebasedatabase.app`
- `PUBLIC_GATEWAY_ORIGIN=https://URL-DU-SERVICE`

Pour la première version, conserver exactement une instance Cloud Run afin que la connexion du Raspberry et celle du navigateur utilisent la même mémoire. Une version multi-instance nécessitera un bus de relais partagé.

## Raspberry

Ajouter dans `/etc/etr-core/firebase-bridge.env` :

```
ETR_REMOTE_GATEWAY_WSS=wss://URL-DU-SERVICE/device
```

Puis relancer `setup_etr.sh`.
