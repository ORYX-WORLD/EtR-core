# Mise en service réseau d'un EtR

## Parcours au démarrage

La mise en service client ne nécessite aucun ordinateur. L'image EtR livrée
contient déjà le portail, NetworkManager et les services d'affichage.

1. **Ethernet disponible** : il reste prioritaire et l'EtR est immédiatement
   joignable. Le portail Wi-Fi reste disponible sur
   `http://etr-core.local:8090` pour mémoriser un réseau sans débrancher le
   câble.
2. **Wi-Fi déjà mémorisé** : NetworkManager le reconnecte automatiquement.
3. **Aucune connexion** : après le délai de démarrage, l'EtR crée le point
   d'accès temporaire `EtR-Setup-XXXX`. L'écran local affiche le SSID, la clé
   temporaire et le code de configuration, puis ouvre le portail.

Au tout premier démarrage, l'assistant reste affiché même lorsque l'Ethernet
est déjà opérationnel. Le client touche **Continuer avec Ethernet** ou choisit
un Wi-Fi. La validation crée `/var/lib/etr-core/commissioned`; les démarrages
suivants ouvrent directement le tableau de bord.

Le portail scanne les réseaux, affiche leur puissance et leur sécurité, puis
enregistre le profil sélectionné dans NetworkManager. La clé Wi-Fi n'est ni
retournée par l'API, ni écrite dans les journaux EtR.

## Saisie tactile et souris

- Un pavé numérique apparaît pour le code de configuration EtR.
- Un clavier visuel AZERTY apparaît pour la clé Wi-Fi, avec majuscules,
  chiffres, symboles, espace, retour arrière et fermeture.
- Toutes les touches fonctionnent au toucher comme au clic de souris. Un
  clavier physique reste utilisable en parallèle.

## Sécurité

- La modification du Wi-Fi exige le code local à six chiffres conservé dans
  `/var/lib/etr-core/wifi-setup.pin` (droits `0600`).
- L'écran local peut préremplir ce code ; un navigateur distant doit le saisir.
- WPA2 et WPA3 sont acceptés. Les réseaux ouverts restent possibles.
- WEP est refusé par défaut. Le contournement `ETR_ALLOW_WEP=1` existe seulement
  pour une migration temporaire d'un ancien site et doit être retiré ensuite.
- Les arguments sont transmis directement à `nmcli`, sans interpréteur shell.

## Installation / mise à jour

```bash
cd /home/oryx/EtR-core
git pull --ff-only
sudo bash src/deploy/raspi/setup_etr.sh
sudo reboot
```

Contrôle :

```bash
sudo systemctl status etr-wifi-portal --no-pager
sudo journalctl -u etr-wifi-portal -n 50 --no-pager
sudo cat /var/lib/etr-core/wifi-setup.pin
```

Le dernier affichage permet de retrouver le code de configuration depuis une
session SSH autorisée. Ne pas publier ce code.

## Registre avant mise en production

À effectuer après les essais terrain et avant toute utilisation réelle :

- [ ] Régénérer le code de configuration local et les identifiants du hotspot temporaire.
- [ ] Invalider toutes les valeurs exposées pendant les tests et vérifier que les fichiers sont en mode `0600`.
- [ ] Vérifier qu'aucun secret n'a été copié dans la documentation, les tickets, les journaux ou les captures d'écran.
