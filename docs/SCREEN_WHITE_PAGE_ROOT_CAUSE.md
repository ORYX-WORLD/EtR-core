# Écran tactile EtR — page blanche dans le tableau embarqué

Date : 2026-08-02

## Symptôme observé

Le portail tactile local reste visible et le bouton **Bureau Linux** répond, mais la zone centrale contenant le tableau EtR affiche une page blanche avec l’icône de document en erreur.

## Cause racine

Le kiosque charge le portail sur :

```text
http://127.0.0.1:8090
```

Le portail affiche ensuite le dashboard local dans un `iframe` :

```text
http://127.0.0.1:8000
```

Le dashboard envoyait pourtant cette directive CSP :

```text
frame-ancestors 'none'
```

Cette directive interdit toute intégration dans un `iframe`, y compris depuis le portail local EtR. Les contrôles HTTP retournaient bien 200, mais ils ne validaient pas le rendu imbriqué dans Chromium.

## Correction

Le dashboard n’autorise désormais qu’un seul parent :

```text
frame-ancestors http://127.0.0.1:8090
```

Les deux services restent liés exclusivement à la boucle locale :

- dashboard : `127.0.0.1:8000` ;
- portail/kiosque : `127.0.0.1:8090` dans Chromium ;
- aucune origine distante n’est autorisée à encadrer le dashboard.

Le kiosque force aussi la langue française et désactive explicitement l’interface de traduction Chromium afin de supprimer la barre `French / English` visible sur l’écran.

## Preuves exigées

- test Python du CSP exact ;
- absence de `frame-ancestors 'none'` ;
- version dashboard incrémentée ;
- présence des drapeaux Chromium `--lang=fr-FR` et `--disable-features=Translate,TranslateUI` ;
- déploiement physique avec services dashboard, portail et kiosque actifs ;
- contrôle HTTP 200 conservé.
