#!/usr/bin/env python3
"""Portail local de mise en service réseau pour un boîtier EtR.

Le service s'appuie sur NetworkManager via ``nmcli``. Il ne publie jamais la
clé Wi-Fi, ne l'écrit pas dans ses journaux et n'utilise aucune commande shell.
"""

from __future__ import annotations

import os
import re
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, render_template_string, request


APP = Flask(__name__)
STATE_DIR = Path(os.environ.get("ETR_STATE_DIR", "/var/lib/etr-core"))
PIN_FILE = STATE_DIR / "wifi-setup.pin"
COMMISSIONED_FILE = STATE_DIR / "commissioned"
HOTSPOT_NAME = "etr-setup"
WIFI_DEVICE = os.environ.get("ETR_WIFI_DEVICE", "wlan0")
PORT = int(os.environ.get("ETR_WIFI_PORT", "8090"))


def run_nmcli(*args: str, timeout: int = 35, check: bool = True) -> str:
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    result = subprocess.run(
        ["nmcli", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    if check and result.returncode:
        detail = (result.stderr or result.stdout or "NetworkManager error").strip()
        raise RuntimeError(detail)
    return result.stdout.strip()


def hardware_serial() -> str:
    for path in (
        Path("/sys/firmware/devicetree/base/serial-number"),
        Path("/proc/device-tree/serial-number"),
    ):
        try:
            value = path.read_text(encoding="utf-8", errors="ignore").strip("\x00\n ")
            if value:
                return re.sub(r"[^0-9a-fA-F]", "", value).lower()
        except OSError:
            pass
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("serial"):
                return re.sub(r"[^0-9a-fA-F]", "", line.split(":", 1)[1]).lower()
    except OSError:
        pass
    return "unknown"


def setup_pin() -> str:
    configured = os.environ.get("ETR_WIFI_SETUP_PIN", "").strip()
    if configured:
        return configured
    try:
        current = PIN_FILE.read_text(encoding="utf-8").strip()
        if current:
            return current
    except OSError:
        pass
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    value = "".join(secrets.choice("0123456789") for _ in range(6))
    PIN_FILE.write_text(value + "\n", encoding="utf-8")
    PIN_FILE.chmod(0o600)
    return value


SETUP_PIN = setup_pin()
SERIAL_SUFFIX = (hardware_serial()[-4:] or "ETR0").upper()
HOTSPOT_SSID = os.environ.get("ETR_SETUP_SSID", f"EtR-Setup-{SERIAL_SUFFIX}")
HOTSPOT_PASSWORD = os.environ.get("ETR_SETUP_WIFI_PASSWORD", f"EtR-{SETUP_PIN}")


def is_loopback() -> bool:
    return (request.remote_addr or "") in {"127.0.0.1", "::1"}


def supplied_pin(payload: dict[str, Any] | None = None) -> str:
    payload = payload or {}
    return str(
        request.headers.get("X-EtR-Setup-Pin")
        or payload.get("pin")
        or request.args.get("pin")
        or ""
    ).strip()


def authorized(payload: dict[str, Any] | None = None) -> bool:
    return is_loopback() or secrets.compare_digest(supplied_pin(payload), SETUP_PIN)


def split_terse(line: str) -> list[str]:
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    fields.append("".join(current))
    return fields


def scan_networks() -> list[dict[str, Any]]:
    run_nmcli("device", "wifi", "rescan", "ifname", WIFI_DEVICE, check=False)
    output = run_nmcli(
        "-t", "--escape", "yes", "-f", "SSID,SIGNAL,SECURITY,IN-USE",
        "device", "wifi", "list", "ifname", WIFI_DEVICE,
    )
    networks: dict[str, dict[str, Any]] = {}
    for line in output.splitlines():
        fields = split_terse(line)
        if len(fields) < 4:
            continue
        ssid, signal_text, security, in_use = fields[:4]
        ssid = ssid.strip()
        if not ssid:
            continue
        try:
            signal = max(0, min(100, int(signal_text)))
        except ValueError:
            signal = 0
        item = {
            "ssid": ssid,
            "signal": signal,
            "security": security.strip() or "Ouvert",
            "connected": in_use.strip() == "*",
        }
        previous = networks.get(ssid)
        if previous is None or signal > int(previous["signal"]):
            networks[ssid] = item
    return sorted(networks.values(), key=lambda item: (-item["connected"], -item["signal"], item["ssid"].lower()))


def active_connection() -> dict[str, Any]:
    output = run_nmcli("-t", "--escape", "yes", "-f", "NAME,TYPE,DEVICE", "connection", "show", "--active", check=False)
    ethernet = False
    wifi_name = ""
    hotspot = False
    for line in output.splitlines():
        fields = split_terse(line)
        if len(fields) < 3:
            continue
        name, kind, device = fields[:3]
        if kind in {"802-3-ethernet", "ethernet"} and device:
            ethernet = True
        if kind in {"802-11-wireless", "wifi"} and device == WIFI_DEVICE:
            hotspot = name == HOTSPOT_NAME
            if not hotspot:
                wifi_name = name
    # Une connexion exploitable signifie ici une liaison active réelle, et non
    # simplement que NetworkManager a terminé son démarrage.
    online = ethernet or bool(wifi_name)
    return {
        "online": online,
        "ethernet": ethernet,
        "wifi": wifi_name,
        "hotspot": hotspot,
        "commissioned": COMMISSIONED_FILE.exists(),
    }

def dashboard_ready() -> bool:
    """Vérifie localement que le tableau EtR répond avant de l'afficher."""
    try:
        with socket.create_connection(("127.0.0.1", 8000), timeout=1.5):
            return True
    except OSError:
        return False



def mark_commissioned() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    COMMISSIONED_FILE.write_text("commissioned\n", encoding="utf-8")
    COMMISSIONED_FILE.chmod(0o644)


def ensure_hotspot() -> None:
    state = active_connection()
    if state["online"] or state["hotspot"]:
        return
    run_nmcli("radio", "wifi", "on")
    run_nmcli("connection", "delete", HOTSPOT_NAME, check=False)
    run_nmcli(
        "device", "wifi", "hotspot", "ifname", WIFI_DEVICE,
        "con-name", HOTSPOT_NAME, "ssid", HOTSPOT_SSID,
        "password", HOTSPOT_PASSWORD,
    )


def restore_hotspot_after_failure() -> None:
    """Rétablit le réseau de configuration si une tentative Wi-Fi échoue."""
    time.sleep(2)
    try:
        ensure_hotspot()
    except Exception as exc:
        print(f"EtR Wi-Fi: restauration du hotspot impossible: {exc}", flush=True)


def bootstrap_network() -> None:
    time.sleep(int(os.environ.get("ETR_WIFI_BOOT_GRACE", "20")))
    try:
        ensure_hotspot()
    except Exception as exc:  # service log, sans aucune clé réseau
        print(f"EtR Wi-Fi: hotspot non démarré: {exc}", flush=True)


PAGE = r"""
<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connexion réseau EtR</title>
<style>
:root{color-scheme:dark;--bg:#041321;--card:#0c263b;--line:#22506c;--cyan:#42e9f5;--lime:#a8ff4f;--text:#f4f8fb;--muted:#a7bfd0}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 80% 10%,#123d5c 0,#041321 48%);color:var(--text);font:16px/1.45 system-ui,sans-serif;min-height:100vh}
main{width:min(780px,calc(100% - 28px));margin:auto;padding:32px 0 60px}.brand{letter-spacing:.16em;font-weight:900}.brand span{color:var(--cyan)}
h1{font-size:clamp(2rem,7vw,4rem);line-height:1;margin:.35em 0}.intro{color:var(--muted);max-width:62ch}.card{background:rgba(12,38,59,.92);border:1px solid var(--line);border-radius:22px;padding:20px;margin-top:20px;box-shadow:0 20px 60px #0006}
.status{display:flex;gap:10px;flex-wrap:wrap}.pill{border:1px solid var(--line);border-radius:99px;padding:7px 11px;color:var(--muted)}.pill.ok{border-color:#62b443;color:#dfffc6}
label{display:block;font-weight:750;margin:14px 0 7px}input,button{font:inherit}input{width:100%;padding:13px 14px;border:1px solid var(--line);border-radius:12px;background:#061a2a;color:var(--text)}
button{border:0;border-radius:99px;padding:12px 18px;font-weight:850;cursor:pointer;background:var(--lime);color:#07131c}button.secondary{background:transparent;color:var(--text);border:1px solid var(--line)}button:disabled{opacity:.55;cursor:wait}
.actions{display:flex;gap:10px;flex-wrap:wrap;margin-top:14px}.networks{display:grid;gap:10px;margin-top:14px}.network{width:100%;border-radius:15px;background:#071d2e;border:1px solid var(--line);color:var(--text);display:grid;grid-template-columns:1fr auto;gap:8px;text-align:left;padding:13px 15px}.network strong{display:block}.network small{color:var(--muted)}
.network.selected{outline:2px solid var(--cyan)}.signal{color:var(--cyan)}.message{min-height:1.5em;color:var(--muted)}.warning{color:#ffd28a}.secret{display:none}.local-code{border-left:3px solid var(--cyan);padding-left:12px;color:var(--muted)}
.keyboard{position:sticky;bottom:8px;z-index:20;margin-top:18px;padding:10px;background:#03101bd9;border:1px solid var(--line);border-radius:16px;backdrop-filter:blur(12px)}.keyrow{display:flex;justify-content:center;gap:6px;margin:6px 0}.key{min-width:42px;min-height:44px;padding:8px 10px;border-radius:10px;background:#13344c;color:var(--text);border:1px solid #2d607d}.key.wide{min-width:86px}.key.active{outline:2px solid var(--cyan)}
.dashboard-shell{position:fixed;inset:0;z-index:50;background:#041321}.dashboard-shell iframe{width:100%;height:100%;border:0;background:#041321}.network-settings{position:fixed;z-index:51;right:18px;top:18px;background:#0c263bee;color:var(--text);border:1px solid var(--cyan);box-shadow:0 8px 30px #0008}
@media(max-width:520px){main{padding-top:20px}.card{padding:16px;border-radius:17px}.actions button{width:100%}.network-settings{width:auto!important;right:10px;top:10px;padding:10px 14px}}
</style></head><body><main>
<div class="brand"><span>ORYX</span> · EtR PROJECT</div><h1>Connecter cet EtR</h1>
<p class="intro">Ethernet est prioritaire. Pour utiliser le Wi-Fi, choisissez un réseau puis saisissez sa clé. La clé reste uniquement dans NetworkManager sur cet EtR.</p>
<section class="card"><div id="status" class="status"><span class="pill">Vérification du réseau…</span></div><p id="localCode" class="local-code" hidden></p>
<div class="actions"><button id="useEthernet" hidden>Continuer avec Ethernet</button></div></section>
<section class="card"><label for="pin">Code de configuration EtR</label><input id="pin" inputmode="numeric" autocomplete="one-time-code" placeholder="6 chiffres">
<div class="actions"><button id="scan">Rechercher les réseaux</button><button id="refresh" class="secondary">Actualiser l’état</button></div><p id="message" class="message"></p><div id="networks" class="networks"></div></section>
<section id="credentials" class="card secret"><h2 id="chosen">Réseau Wi-Fi</h2><form id="connect"><input type="hidden" id="ssid"><input type="hidden" id="security">
<label for="password">Clé Wi-Fi</label><input id="password" type="password" autocomplete="new-password" placeholder="WPA2 / WPA3">
<div class="actions"><button type="button" id="showPassword" class="secondary">Afficher la clé</button><button type="submit">Se connecter</button><button type="button" id="cancel" class="secondary">Annuler</button></div></form></section>
<section id="keyboard" class="keyboard" hidden aria-label="Clavier tactile"></section>
</main>
<section id="dashboardShell" class="dashboard-shell" hidden aria-label="Tableau de bord EtR">
  <iframe id="dashboardFrame" title="Écran de supervision EtR" src="about:blank"></iframe>
  <button id="networkSettings" class="network-settings" type="button">Réseau</button>
</section>
<script>
const $=s=>document.querySelector(s), pin=$('#pin'), msg=$('#message'), networks=$('#networks'), credentials=$('#credentials'), keyboard=$('#keyboard'), dashboardShell=$('#dashboardShell'), dashboardFrame=$('#dashboardFrame');let keyTarget=null,keyShift=false,keySymbols=false;
async function api(path,options={}){const body=options.body?JSON.parse(options.body):{};body.pin=pin.value.trim();options.body=options.body?JSON.stringify(body):undefined;options.headers={'Content-Type':'application/json',...(options.headers||{})};const r=await fetch(path,options);const data=await r.json();if(!r.ok)throw new Error(data.error||'Opération impossible');return data}
function showStatus(s){const items=[];items.push(`<span class="pill ${s.online?'ok':''}">${s.online?'En ligne':'Hors ligne'}</span>`);if(s.ethernet)items.push('<span class="pill ok">Ethernet connecté</span>');if(s.wifi)items.push(`<span class="pill ok">Wi-Fi : ${esc(s.wifi)}</span>`);if(s.hotspot)items.push('<span class="pill">Mode configuration actif</span>');if(s.commissioned)items.push('<span class="pill ok">EtR configuré</span>');$('#status').innerHTML=items.join('');$('#useEthernet').hidden=!s.ethernet||s.commissioned}
function esc(v){const d=document.createElement('div');d.textContent=v;return d.innerHTML}
async function dashboardIsReady(){try{const r=await fetch('/api/dashboard-ready',{cache:'no-store'});if(!r.ok)return false;return !!(await r.json()).ready}catch(e){return false}}
function showDashboard(){keyboard.hidden=true;dashboardFrame.src='http://127.0.0.1:8000';dashboardShell.hidden=false}
async function waitDashboard(retry=false){const attempts=retry?180:1;for(let i=0;i<attempts;i++){if(await dashboardIsReady()){showDashboard();return true}if(retry){msg.textContent='Connexion confirmée. Démarrage du tableau EtR…';await new Promise(r=>setTimeout(r,1000))}}msg.textContent='Le réseau est connecté. Le tableau EtR démarre encore : vous pouvez réessayer avec Actualiser.';return false}
async function status(){try{const s=await api('/api/status');showStatus(s);if(s.commissioned&&s.online&&!s.hotspot&&location.hostname==='127.0.0.1')await waitDashboard(false)}catch(e){msg.textContent=e.message}}
async function localInfo(){try{const r=await fetch('/api/local-info');if(!r.ok)return;const d=await r.json();pin.value=d.pin;$('#localCode').hidden=false;$('#localCode').textContent=`Réseau temporaire : ${d.hotspotSsid} · clé : ${d.hotspotPassword} · code : ${d.pin}`;}catch(e){}}
async function scan(){msg.textContent='Recherche en cours…';networks.innerHTML='';try{const list=await api('/api/networks');list.forEach(n=>{const b=document.createElement('button');b.type='button';b.className='network';b.innerHTML=`<span><strong>${esc(n.ssid)}</strong><small>${esc(n.security)}${n.connected?' · connecté':''}</small></span><span class="signal">${n.signal}%</span>`;b.onclick=()=>selectNetwork(n,b);networks.appendChild(b)});msg.textContent=list.length?`${list.length} réseau(x) détecté(s).`:'Aucun réseau détecté.'}catch(e){msg.textContent=e.message}}
function selectNetwork(n,b){document.querySelectorAll('.network').forEach(x=>x.classList.remove('selected'));b.classList.add('selected');$('#ssid').value=n.ssid;$('#security').value=n.security;$('#chosen').textContent=n.ssid;const open=n.security==='Ouvert'||n.security==='--';$('#password').disabled=open;$('#password').placeholder=open?'Réseau ouvert':'Clé WPA2 / WPA3';credentials.classList.remove('secret');credentials.scrollIntoView({behavior:'smooth'})}
function keyButton(label,value=label,wide=false){const b=document.createElement('button');b.type='button';b.className='key'+(wide?' wide':'');b.textContent=label;b.onpointerdown=e=>{e.preventDefault();keyPress(value)};return b}
function drawKeyboard(){keyboard.innerHTML='';if(!keyTarget)return;let rows;if(keyTarget===pin){rows=[['1','2','3'],['4','5','6'],['7','8','9'],['Effacer','0','Fermer']]}else if(keySymbols){rows=[["1","2","3","4","5","6","7","8","9","0"],["@","#","%","&","*","-","_","+","="],[".",",",":",";","/","\\","!","?"],["'","\"","^","$","€","£","~","|"],["[","]","{","}","<",">","(",")"],["ABC","Espace","⌫","Fermer"]]}else{const a=keyShift?'AZERTYUIOP':'azertyuiop',b=keyShift?'QSDFGHJKLM':'qsdfghjklm',c=keyShift?'WXCVBN':'wxcvbn';rows=[[...a],[...b],[...c],['Maj','123/@','Espace','⌫','Fermer']]}rows.forEach(row=>{const line=document.createElement('div');line.className='keyrow';row.forEach(k=>line.appendChild(keyButton(k,k,['Effacer','Espace','Fermer','123/@'].includes(k))));keyboard.appendChild(line)});keyboard.hidden=false}
function keyPress(k){if(!keyTarget)return;if(k==='Fermer'){keyboard.hidden=true;keyTarget.blur();return}if(k==='Maj'){keyShift=!keyShift;drawKeyboard();return}if(k==='123/@'||k==='ABC'){keySymbols=!keySymbols;drawKeyboard();return}if(k==='⌫'){keyTarget.value=keyTarget.value.slice(0,-1)}else if(k==='Effacer'){keyTarget.value=''}else if(k==='Espace'){keyTarget.value+=' '}else{keyTarget.value+=k}keyTarget.dispatchEvent(new Event('input',{bubbles:true}));keyTarget.focus({preventScroll:true})}
function offerKeyboard(input){input.addEventListener('focus',()=>{keyTarget=input;keySymbols=false;keyShift=false;drawKeyboard()});input.addEventListener('pointerdown',()=>{keyTarget=input;drawKeyboard()})}
offerKeyboard(pin);offerKeyboard($('#password'));
$('#scan').onclick=scan;$('#refresh').onclick=status;$('#cancel').onclick=()=>credentials.classList.add('secret');
$('#showPassword').onclick=()=>{const p=$('#password'),visible=p.type==='text';p.type=visible?'password':'text';$('#showPassword').textContent=visible?'Afficher la clé':'Masquer la clé';p.focus({preventScroll:true})};
$('#useEthernet').onclick=async()=>{msg.textContent='Validation de la connexion Ethernet…';try{await api('/api/complete',{method:'POST',body:'{}'});msg.textContent='EtR configuré. Démarrage du tableau de bord…';await waitDashboard(true)}catch(e){msg.textContent=e.message}};
$('#connect').onsubmit=async e=>{e.preventDefault();msg.textContent='Connexion en cours…';const payload={ssid:$('#ssid').value,password:$('#password').value,security:$('#security').value,pin:pin.value.trim()};try{await api('/api/connect',{method:'POST',body:JSON.stringify(payload)});$('#password').value='';keyboard.hidden=true;msg.textContent='Connexion Wi-Fi confirmée. Démarrage du tableau EtR…';await waitDashboard(true)}catch(err){msg.textContent=err.message;setTimeout(status,2500)}};
$('#networkSettings').onclick=()=>{dashboardShell.hidden=true;dashboardFrame.src='about:blank';status()};
localInfo().then(status);
</script></body></html>
"""


@APP.get("/")
def index():
    return render_template_string(PAGE)


@APP.get("/api/local-info")
def local_info():
    if not is_loopback():
        return jsonify({"error": "Disponible uniquement sur l'écran EtR"}), 403
    return jsonify({"pin": SETUP_PIN, "hotspotSsid": HOTSPOT_SSID, "hotspotPassword": HOTSPOT_PASSWORD})


@APP.get("/api/status")
def status():
    # L'état n'expose ni mot de passe, ni numéro de série brut.
    return jsonify(active_connection())


@APP.get("/api/dashboard-ready")
def dashboard_status():
    return jsonify({"ready": dashboard_ready()})


@APP.get("/api/networks")
def networks():
    if not authorized():
        return jsonify({"error": "Code de configuration incorrect"}), 403
    try:
        return jsonify(scan_networks())
    except Exception as exc:
        return jsonify({"error": f"Recherche Wi-Fi impossible : {exc}"}), 503


@APP.post("/api/complete")
def complete():
    payload = request.get_json(silent=True) or {}
    if not authorized(payload):
        return jsonify({"error": "Code de configuration incorrect"}), 403
    state = active_connection()
    if not state["ethernet"] and not state["wifi"]:
        return jsonify({"error": "Aucune connexion réseau utilisable"}), 409
    mark_commissioned()
    return jsonify({"ok": True})


@APP.post("/api/connect")
def connect():
    payload = request.get_json(silent=True) or {}
    if not authorized(payload):
        return jsonify({"error": "Code de configuration incorrect"}), 403
    ssid = str(payload.get("ssid", "")).strip()
    password = str(payload.get("password", ""))
    security = str(payload.get("security", ""))
    if not ssid or len(ssid.encode("utf-8")) > 32:
        return jsonify({"error": "Nom de réseau Wi-Fi invalide"}), 400
    if "WEP" in security.upper() and os.environ.get("ETR_ALLOW_WEP") != "1":
        return jsonify({"error": "Le WEP est désactivé car il n'est pas sécurisé. Utilisez WPA2 ou WPA3."}), 400
    if security not in {"", "--", "Ouvert"} and len(password) < 8:
        return jsonify({"error": "La clé Wi-Fi doit contenir au moins 8 caractères"}), 400
    try:
        # Supprime un éventuel profil portant le même nom : NetworkManager
        # ne doit jamais réutiliser silencieusement une ancienne mauvaise clé.
        run_nmcli("radio", "wifi", "on")
        run_nmcli("connection", "delete", "id", ssid, check=False)
        args = ["device", "wifi", "connect", ssid, "ifname", WIFI_DEVICE]
        if password:
            args.extend(["password", password])
        run_nmcli(*args, timeout=55)
        # nmcli peut rendre la main avant que l'état actif soit stabilisé.
        # On ne valide jamais l'EtR tant que wlan0 n'a pas réellement quitté
        # le hotspot pour une connexion Wi-Fi active.
        deadline = time.monotonic() + 25
        connected_name = ""
        while time.monotonic() < deadline:
            state = active_connection()
            if state["wifi"] and not state["hotspot"]:
                connected_name = str(state["wifi"])
                break
            time.sleep(1)
        if not connected_name:
            raise RuntimeError("le réseau n'a pas confirmé la connexion")
        mark_commissioned()
        return jsonify({
            "ok": True,
            "ssid": ssid,
            "redirect": "http://127.0.0.1:8000",
        })
    except Exception as exc:
        # Une clé refusée peut couper le hotspot de configuration. Sa
        # restauration en arrière-plan permet de réessayer sans ordinateur.
        threading.Thread(target=restore_hotspot_after_failure, daemon=True).start()
        return jsonify({"error": f"Connexion refusée : {exc}"}), 400


if __name__ == "__main__":
    threading.Thread(target=bootstrap_network, daemon=True).start()
    APP.run(host="0.0.0.0", port=PORT, threaded=True)
,'€','£','~','|'],['[',']','{','}','<','>','(',')'],['ABC','Espace','⌫','Fermer']]}else{const a=keyShift?'AZERTYUIOP':'azertyuiop',b=keyShift?'QSDFGHJKLM':'qsdfghjklm',c=keyShift?'WXCVBN':'wxcvbn';rows=[[...a],[...b],[...c],['Maj','123/@','Espace','⌫','Fermer']]}rows.forEach(row=>{const line=document.createElement('div');line.className='keyrow';row.forEach(k=>line.appendChild(keyButton(k,k,['Effacer','Espace','Fermer','123/@'].includes(k))));keyboard.appendChild(line)});keyboard.hidden=false}
function keyPress(k){if(!keyTarget)return;if(k==='Fermer'){keyboard.hidden=true;keyTarget.blur();return}if(k==='Maj'){keyShift=!keyShift;drawKeyboard();return}if(k==='123/@'||k==='ABC'){keySymbols=!keySymbols;drawKeyboard();return}if(k==='⌫'){keyTarget.value=keyTarget.value.slice(0,-1)}else if(k==='Effacer'){keyTarget.value=''}else if(k==='Espace'){keyTarget.value+=' '}else{keyTarget.value+=k}keyTarget.dispatchEvent(new Event('input',{bubbles:true}));keyTarget.focus({preventScroll:true})}
function offerKeyboard(input){input.addEventListener('focus',()=>{keyTarget=input;keySymbols=false;keyShift=false;drawKeyboard()});input.addEventListener('pointerdown',()=>{keyTarget=input;drawKeyboard()})}
offerKeyboard(pin);offerKeyboard($('#password'));
$('#scan').onclick=scan;$('#refresh').onclick=status;$('#cancel').onclick=()=>credentials.classList.add('secret');
$('#useEthernet').onclick=async()=>{msg.textContent='Validation de la connexion Ethernet…';try{await api('/api/complete',{method:'POST',body:'{}'});msg.textContent='EtR configuré. Démarrage du tableau de bord…';await waitDashboard(true)}catch(e){msg.textContent=e.message}};
$('#connect').onsubmit=async e=>{e.preventDefault();msg.textContent='Connexion en cours…';const payload={ssid:$('#ssid').value,password:$('#password').value,security:$('#security').value,pin:pin.value.trim()};try{await api('/api/connect',{method:'POST',body:JSON.stringify(payload)});$('#password').value='';keyboard.hidden=true;msg.textContent='Connexion Wi-Fi confirmée. Démarrage du tableau EtR…';await waitDashboard(true)}catch(err){msg.textContent=err.message;setTimeout(status,2500)}};
$('#networkSettings').onclick=()=>{dashboardShell.hidden=true;dashboardFrame.src='about:blank';status()};
localInfo().then(status);
</script></body></html>
"""


@APP.get("/")
def index():
    return render_template_string(PAGE)


@APP.get("/api/local-info")
def local_info():
    if not is_loopback():
        return jsonify({"error": "Disponible uniquement sur l'écran EtR"}), 403
    return jsonify({"pin": SETUP_PIN, "hotspotSsid": HOTSPOT_SSID, "hotspotPassword": HOTSPOT_PASSWORD})


@APP.get("/api/status")
def status():
    # L'état n'expose ni mot de passe, ni numéro de série brut.
    return jsonify(active_connection())


@APP.get("/api/dashboard-ready")
def dashboard_status():
    return jsonify({"ready": dashboard_ready()})


@APP.get("/api/networks")
def networks():
    if not authorized():
        return jsonify({"error": "Code de configuration incorrect"}), 403
    try:
        return jsonify(scan_networks())
    except Exception as exc:
        return jsonify({"error": f"Recherche Wi-Fi impossible : {exc}"}), 503


@APP.post("/api/complete")
def complete():
    payload = request.get_json(silent=True) or {}
    if not authorized(payload):
        return jsonify({"error": "Code de configuration incorrect"}), 403
    state = active_connection()
    if not state["ethernet"] and not state["wifi"]:
        return jsonify({"error": "Aucune connexion réseau utilisable"}), 409
    mark_commissioned()
    return jsonify({"ok": True})


@APP.post("/api/connect")
def connect():
    payload = request.get_json(silent=True) or {}
    if not authorized(payload):
        return jsonify({"error": "Code de configuration incorrect"}), 403
    ssid = str(payload.get("ssid", "")).strip()
    password = str(payload.get("password", ""))
    security = str(payload.get("security", ""))
    if not ssid or len(ssid.encode("utf-8")) > 32:
        return jsonify({"error": "Nom de réseau Wi-Fi invalide"}), 400
    if "WEP" in security.upper() and os.environ.get("ETR_ALLOW_WEP") != "1":
        return jsonify({"error": "Le WEP est désactivé car il n'est pas sécurisé. Utilisez WPA2 ou WPA3."}), 400
    if security not in {"", "--", "Ouvert"} and len(password) < 8:
        return jsonify({"error": "La clé Wi-Fi doit contenir au moins 8 caractères"}), 400
    try:
        args = ["device", "wifi", "connect", ssid, "ifname", WIFI_DEVICE]
        if password:
            args.extend(["password", password])
        run_nmcli(*args, timeout=55)
        # nmcli peut rendre la main avant que l'état actif soit stabilisé.
        # On ne valide jamais l'EtR tant que wlan0 n'a pas réellement quitté
        # le hotspot pour une connexion Wi-Fi active.
        deadline = time.monotonic() + 25
        connected_name = ""
        while time.monotonic() < deadline:
            state = active_connection()
            if state["wifi"] and not state["hotspot"]:
                connected_name = str(state["wifi"])
                break
            time.sleep(1)
        if not connected_name:
            raise RuntimeError("le réseau n'a pas confirmé la connexion")
        mark_commissioned()
        return jsonify({
            "ok": True,
            "ssid": ssid,
            "redirect": "http://127.0.0.1:8000",
        })
    except Exception as exc:
        # Une clé refusée peut couper le hotspot de configuration. Sa
        # restauration en arrière-plan permet de réessayer sans ordinateur.
        threading.Thread(target=restore_hotspot_after_failure, daemon=True).start()
        return jsonify({"error": f"Connexion refusée : {exc}"}), 400


if __name__ == "__main__":
    threading.Thread(target=bootstrap_network, daemon=True).start()
    APP.run(host="0.0.0.0", port=PORT, threaded=True)
