# Contrat de télémétrie EtR 1.1

Le producteur d'acquisition écrit atomiquement le fichier `/var/lib/etr-core/telemetry.json`. Il écrit un fichier temporaire, le synchronise puis le renomme afin que l'API ne lise jamais un JSON partiel.

```json
{
  "schema_version": "1.1",
  "updated_at": "2026-08-02T08:00:00+00:00",
  "source": "ads1263-home-lab",
  "acquisition_version": "1.0.0",
  "hardware": {
    "adc": "ADS1263",
    "hat": "Waveshare High-Precision AD HAT",
    "status": "online",
    "chip_id": 1,
    "mode": "single_ended_aincom",
    "reference": "AVDD_AVSS"
  },
  "sensors": [
    {
      "id": "pressure_1",
      "label": "Pression CAREL 1",
      "ain": 0,
      "kind": "pressure",
      "status": "ok",
      "signal_v": 0.5,
      "value": 0.0,
      "unit": "bar"
    },
    {
      "id": "temperature_1",
      "label": "Sonde AKO 1",
      "ain": 2,
      "kind": "temperature",
      "status": "reference_resistor_missing_or_probe_open",
      "signal_v": 4.98,
      "value": null,
      "unit": "°C"
    }
  ],
  "measurements": {
    "pressure_1_bar": 0.0,
    "pressure_1_signal_v": 0.5
  },
  "states": {
    "adc_online": true,
    "pressure_1_status": "ok",
    "temperature_1_status": "reference_resistor_missing_or_probe_open"
  },
  "alerts": []
}
```

## Règles

- `updated_at` est une date UTC ISO 8601.
- `hardware` décrit uniquement le matériel réellement détecté. Un ADC inaccessible est déclaré `offline` ; le producteur n'invente aucune mesure de remplacement.
- `sensors` contient les diagnostics des canaux physiques, y compris la tension de signal, la résistance calculée lorsqu'elle est valide et l'état du câblage.
- `measurements` contient uniquement des nombres effectivement mesurés et exploitables, accompagnés d'une unité dans leur nom.
- `states` contient les états discrets normalisés.
- `alerts` contient au maximum les alarmes actives utiles à l'exploitation.
- Une donnée absente reste absente : aucune pression, température ou valeur d'état ne doit être simulée.
- Une entrée NTC sans résistance fixe valide ne publie pas de température. L'état `reference_resistor_missing_or_probe_open` est publié à la place.
- Une NTC dont la résistance est mesurable mais dont la courbe constructeur n'est pas validée publie sa résistance et l'état `curve_required`, sans température calculée.
- Le mapping ADC/Modbus/GPIO/série est propre à une configuration d'installation et doit être versionné séparément de ses secrets d'accès.

## Profil matériel optionnel — 9 septembre 2026 (préparation locale)

Le fichier `/var/lib/etr-core/hardware-profile.json` appartient à l'utilisateur du service (`oryx`, mode 0600). `ETR_HARDWARE_PROFILE_FILE` permet une base isolée pour les tests. Sans fichier, le profil conserve la compatibilité historique : ADS1263 activé, convertisseur Modbus désactivé. Un fichier invalide produit `HARDWARE_PROFILE_INVALID`, sans repli silencieux sur le HAT.

`GET /api/v1/hardware` retourne `{profile}`. `PUT /api/v1/hardware` reçoit le profil complet (`schema_version:1`, `revision`, `ads1263.enabled`, `modbus.enabled`, `modbus.serial_port`). JSON, en-tête `X-ETR-Local-Write: 1` et origine identique si présente sont requis ; aucune ouverture CORS. La sauvegarde compare la révision sous verrou interprocessus, écrit atomiquement et retourne la révision incrémentée ; une écriture concurrente obsolète reçoit 409. Le dashboard relaie via `/api/hardware`, view-store via `/api/hardware/profile` avec contrôle de l'identité du Raspberry configuré et uniquement un transport localhost sans redirection.

Le service relit le profil à chaque cycle. HAT désactivé : aucun accès au fichier de canaux ni à SPI/GPIO, `hardware.status=disabled`, tableaux de capteurs et mesures vides, aucune alerte ADC. HAT activé mais inaccessible : `ADC_UNAVAILABLE`. Les affectations du dessin sont conservées ; les entrées désactivées ne sont plus proposées. Les réseaux TCP restent indépendants du convertisseur USB/RS485.

Chaque trame porte `hardware_profile`. Un changement de profil invalide immédiatement l'ancienne trame dans l'API (`hardware_profile_applying`) ; les nouvelles trames horodatées deviennent indisponibles après 20 secondes. Une absence de mesure n'est jamais transformée en zéro. Le bridge relaie intégralement le contrat, sans ajout de mesures.

Le sous-état `hardware.modbus` distingue `disabled`, `not_configured` (port vide), `unavailable` (port absent), `pending_validation` (port présent). La présence d'un port ne prouve ni un convertisseur fonctionnel ni une réponse du régulateur. Dans cette étape, aucun moteur Modbus n'est livré : `modbus_acquisition=false` et la livraison physique refuse de qualifier une acquisition Modbus non validée. Le preset `config/hardware-modbus-only.json` exprime le choix demandé sans inventer le port. Il n'est pas appliqué à l'installation en service.

L'installateur saute la préparation ADS1263 lorsque le profil la désactive ; il conserve le service de publication d'état. La préparation de l'écran existant reste distincte : nouveau pilote/rotation/tactile non qualifiés ici. La réactivation du HAT sur une installation neuve nécessite ses dépendances SPI/GPIO et la vérification matérielle habituelle. Aucun déploiement, reboot, changement de GPIO ou configuration terrain n'a été effectué pendant cette préparation.
