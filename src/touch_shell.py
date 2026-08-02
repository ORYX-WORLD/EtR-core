#!/usr/bin/env python3
"""Ajoute les commandes tactiles locales au portail EtR.

Le portail Wi-Fi reste la source de vérité pour la mise en service réseau. Cette
surcouche ajoute uniquement deux commandes locales : masquer le kiosque pour
retrouver le bureau Linux et relancer le kiosque depuis le raccourci du bureau.
Les endpoints sont limités à la boucle locale et n'acceptent aucune commande
fournie par le client.
"""

from __future__ import annotations

import subprocess
import threading
import time

from flask import jsonify

import wifi_portal

KIOSK_SERVICE = "etr-kiosk.service"
SYSTEMCTL = "/usr/bin/systemctl"

CONTROL_STYLE = r"""
.etr-linux-button{
  position:fixed;
  z-index:90;
  right:18px;
  bottom:18px;
  display:inline-flex;
  align-items:center;
  gap:9px;
  min-height:48px;
  padding:11px 17px;
  border:2px solid #fff;
  border-radius:999px;
  background:#061a2e;
  color:#fff;
  box-shadow:0 10px 34px #000a;
  font:800 15px/1 system-ui,sans-serif;
  cursor:pointer;
}
.etr-linux-button:hover,.etr-linux-button:focus-visible{
  border-color:#42e9f5;
  outline:3px solid #42e9f566;
  outline-offset:2px;
}
.etr-linux-button:disabled{opacity:.7;cursor:wait}
.etr-linux-button .etr-linux-icon{font-size:20px;line-height:1}
@media(max-width:520px){
  .etr-linux-button{right:10px;bottom:10px;min-height:44px;padding:10px 14px;font-size:13px}
}
"""

CONTROL_HTML = r"""
<button id="etrLinuxDesktop" class="etr-linux-button" type="button" aria-label="Afficher le bureau Linux">
  <span class="etr-linux-icon" aria-hidden="true">▦</span>
  <span>Bureau Linux</span>
</button>
"""

CONTROL_SCRIPT = r"""
<script>
(()=>{
  const button=document.getElementById('etrLinuxDesktop');
  if(!button)return;
  let pending=false;
  const reset=()=>{pending=false;button.disabled=false;button.innerHTML='<span class="etr-linux-icon" aria-hidden="true">▦</span><span>Bureau Linux</span>'};
  button.addEventListener('click',async()=>{
    if(pending)return;
    if(!window.confirm('Afficher le bureau Linux ? Le tableau EtR pourra être rouvert avec le raccourci « Revenir à EtR » sur le bureau.'))return;
    pending=true;
    button.disabled=true;
    button.textContent='Ouverture du bureau…';
    try{
      const response=await fetch('/api/local-ui/desktop',{method:'POST',cache:'no-store',headers:{Accept:'application/json'}});
      const data=await response.json();
      if(!response.ok)throw new Error(data.error||'Commande refusée');
      window.setTimeout(()=>{
        if(document.visibilityState==='visible'){
          reset();
          window.alert('Le bureau Linux ne s’est pas ouvert. Réessayez ou redémarrez l’EtR.');
        }
      },4500);
    }catch(error){
      reset();
      window.alert(error.message||'Impossible d’ouvrir le bureau Linux.');
    }
  });
})();
</script>
"""


def enhance_page(page: str) -> str:
    """Injecte le bouton sans modifier le parcours Wi-Fi existant."""
    if 'id="etrLinuxDesktop"' in page:
        return page
    if "</style>" not in page or "</body>" not in page:
        raise RuntimeError("Structure HTML du portail EtR inattendue")
    page = page.replace("</style>", CONTROL_STYLE + "\n</style>", 1)
    return page.replace("</body>", CONTROL_HTML + CONTROL_SCRIPT + "\n</body>", 1)


def control_kiosk(action: str) -> None:
    """Démarre ou arrête uniquement le service kiosque explicitement autorisé."""
    if action not in {"start", "stop"}:
        raise ValueError("Action kiosque non autorisée")
    result = subprocess.run(
        [SYSTEMCTL, action, KIOSK_SERVICE],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or "systemctl a échoué").strip()
        raise RuntimeError(detail[:500])


def stop_kiosk_after_response() -> None:
    """Laisse au navigateur le temps de recevoir la réponse avant sa fermeture."""
    time.sleep(0.45)
    try:
        control_kiosk("stop")
    except Exception as exc:  # journal local, aucune donnée sensible
        print(f"EtR interface tactile : arrêt du kiosque impossible : {exc}", flush=True)


wifi_portal.PAGE = enhance_page(wifi_portal.PAGE)
APP = wifi_portal.APP


@APP.post("/api/local-ui/desktop")
def show_linux_desktop():
    if not wifi_portal.is_loopback():
        return jsonify({"error": "Commande disponible uniquement sur l'écran EtR"}), 403
    threading.Thread(target=stop_kiosk_after_response, daemon=True).start()
    return jsonify({"ok": True, "mode": "linux-desktop"})


@APP.post("/api/local-ui/dashboard")
def show_etr_dashboard():
    if not wifi_portal.is_loopback():
        return jsonify({"error": "Commande disponible uniquement sur l'écran EtR"}), 403
    try:
        control_kiosk("start")
    except Exception as exc:
        return jsonify({"error": f"Redémarrage du tableau EtR impossible : {exc}"}), 503
    return jsonify({"ok": True, "mode": "etr-dashboard"})


if __name__ == "__main__":
    threading.Thread(target=wifi_portal.bootstrap_network, daemon=True).start()
    APP.run(host="0.0.0.0", port=wifi_portal.PORT, threaded=True)
