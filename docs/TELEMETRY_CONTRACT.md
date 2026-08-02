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
