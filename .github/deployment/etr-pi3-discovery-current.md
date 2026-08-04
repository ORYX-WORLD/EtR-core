# Diagnostic réseau du Raspberry Pi 3

Date UTC : 2026-08-04, de 05:28:46 à 05:32:00.

## Périmètre contrôlé

- Banc `etr-core` connecté en Wi-Fi avec l'adresse `192.168.1.65`.
- Balayage complet du réseau local `192.168.1.0/24` réalisé trois fois.
- Balayage du réseau Tailscale local `100.82.151.0/24`.
- Recherche des ports SSH, HTTP, API EtR, portail EtR et VNC.
- Recherche mDNS/Avahi.
- Recherche des réseaux Wi-Fi environnants.
- Contrôle de la passerelle distante EtR.

## Résultats

- Aucun appareil présentant un nom `raspberrypi`, un nom commençant par `etr-`, une API EtR ou un préfixe MAC Raspberry Pi n'a été détecté.
- Aucun service mDNS nouveau n'a été détecté ; seul `etr-core.local` est annoncé.
- Aucun portail ou point d'accès Wi-Fi EtR n'a été détecté ; le seul SSID observé est `MERCUSYS_907C`.
- Tailscale ne présente que `etr-core` en ligne ; aucun Raspberry Pi 3 supplémentaire n'est connecté.
- La passerelle distante répond correctement mais annonce `devices: 1`, ce qui correspond au seul banc `etr-core` déjà connu.

## Conclusion

Le Raspberry Pi 3 n'est pas visible actuellement, ni sur le réseau local, ni par mDNS, ni comme portail Wi-Fi EtR, ni sur Tailscale, ni dans la passerelle distante.

Ce résultat ne prouve pas que la carte est hors tension. Il prouve que le Pi 3 n'a pas obtenu de connexion réseau exploitable et n'a pas terminé une initialisation EtR observable. Les causes les plus probables sont : carte non amorçable ou incomplète, démarrage bloqué, alimentation insuffisante, ou absence de configuration réseau exploitable.
