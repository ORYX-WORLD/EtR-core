import crypto from "node:crypto";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";
import admin from "firebase-admin";
import { WebSocketServer, WebSocket } from "ws";

const PORT = Number(process.env.PORT || 8080);
const ALLOWED_ORIGINS = new Set(
  (process.env.ALLOWED_ORIGINS || "https://oryx-froid-industriel.web.app")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean)
);
const DATABASE_URL =
  process.env.FIREBASE_DATABASE_URL ||
  "https://oryx-froid-industriel-default-rtdb.europe-west1.firebasedatabase.app";

admin.initializeApp({ credential: admin.credential.applicationDefault(), databaseURL: DATABASE_URL });
const db = admin.database();
const app = express();
const server = http.createServer(app);
const wss = new WebSocketServer({ noServer: true, maxPayload: 2 * 1024 * 1024 });
const devices = new Map();
const tickets = new Map();
const __dirname = path.dirname(fileURLToPath(import.meta.url));

app.disable("x-powered-by");
app.use(express.json({ limit: "32kb" }));
app.use("/novnc", express.static(path.join(__dirname, "node_modules", "@novnc", "novnc"), {
  immutable: true,
  maxAge: "1d"
}));

function originAllowed(origin) {
  return !origin || ALLOWED_ORIGINS.has(origin);
}

function bearer(req) {
  const value = req.headers.authorization || "";
  return value.startsWith("Bearer ") ? value.slice(7) : "";
}

async function verifyIdToken(token) {
  if (!token) throw Object.assign(new Error("Jeton manquant"), { status: 401 });
  return admin.auth().verifyIdToken(token, true);
}

async function clientCanView(decoded, installationId) {
  if (decoded.oryxStaff === true || decoded.oryxDeveloper === true) return true;
  const snap = await db.ref(`memberships/${decoded.uid}/${installationId}`).get();
  return snap.child("active").val() === true;
}

async function deviceCanConnect(decoded, installationId) {
  const snap = await db.ref(`deviceAccess/${decoded.uid}`).get();
  return snap.val() === installationId;
}

function jsonError(res, error) {
  const status = Number(error.status || 500);
  res.status(status).json({ error: status >= 500 ? "Erreur de passerelle" : error.message });
}

app.use((req, res, next) => {
  const origin = req.headers.origin;
  if (!originAllowed(origin)) return res.status(403).json({ error: "Origine refusée" });
  if (origin) {
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Vary", "Origin");
    res.setHeader("Access-Control-Allow-Headers", "authorization, content-type");
    res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  }
  if (req.method === "OPTIONS") return res.sendStatus(204);
  next();
});

app.get("/healthz", (_req, res) => {
  res.json({ ok: true, devices: devices.size });
});

app.post("/api/remote-session", async (req, res) => {
  try {
    const decoded = await verifyIdToken(bearer(req));
    const installationId = String(req.body?.installationId || "").trim();
    if (!/^[A-Za-z0-9._-]{2,80}$/.test(installationId)) {
      return res.status(400).json({ error: "Installation invalide" });
    }
    if (!(await clientCanView(decoded, installationId))) {
      return res.status(403).json({ error: "Accès non autorisé à cette installation" });
    }
    const device = devices.get(installationId);
    if (!device || device.readyState !== WebSocket.OPEN) {
      return res.status(409).json({ error: "EtR hors ligne ou passerelle non configurée" });
    }
    const ticket = crypto.randomBytes(32).toString("base64url");
    tickets.set(ticket, {
      installationId,
      uid: decoded.uid,
      expiresAt: Date.now() + 60_000
    });
    const gatewayOrigin = process.env.PUBLIC_GATEWAY_ORIGIN || `${req.protocol}://${req.get("host")}`;
    res.json({
      viewerUrl: `${gatewayOrigin}/viewer?ticket=${encodeURIComponent(ticket)}`,
      expiresIn: 60
    });
  } catch (error) {
    jsonError(res, error);
  }
});

app.get("/viewer", (req, res) => {
  const origin = [...ALLOWED_ORIGINS].map((value) => value.replace(/'/g, "")).join(" ");
  res.setHeader("Cache-Control", "no-store");
  res.setHeader(
    "Content-Security-Policy",
    `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' wss:; img-src 'self' data:; frame-ancestors ${origin}`
  );
  res.type("html").send(`<!doctype html>
<html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Écran EtR</title><style>
html,body,#screen{width:100%;height:100%;margin:0;background:#03101f;overflow:hidden}
#status{position:fixed;z-index:3;inset:16px auto auto 50%;transform:translateX(-50%);padding:9px 14px;border-radius:999px;background:#071827e8;color:#dff8ff;font:600 14px system-ui;border:1px solid #1e7890}
</style></head><body><div id="status">Connexion sécurisée à l’EtR…</div><div id="screen"></div>
<script type="module">
import RFB from "/novnc/core/rfb.js";
const status=document.getElementById("status");
const ticket=new URLSearchParams(location.search).get("ticket")||"";
const scheme=location.protocol==="https:"?"wss":"ws";
const url=scheme+"://"+location.host+"/client?ticket="+encodeURIComponent(ticket);
try{
 const rfb=new RFB(document.getElementById("screen"),url,{shared:true});
 rfb.scaleViewport=true;rfb.resizeSession=false;rfb.showDotCursor=true;
 rfb.addEventListener("connect",()=>{status.textContent="EtR connecté";setTimeout(()=>status.remove(),1200)});
 rfb.addEventListener("disconnect",(event)=>{status.textContent=event.detail.clean?"Session terminée":"Connexion interrompue"});
 rfb.addEventListener("credentialsrequired",()=>{status.textContent="Authentification locale non attendue"});
}catch(error){status.textContent="Impossible d’ouvrir l’écran EtR"}
</script></body></html>`);
});

server.on("upgrade", async (req, socket, head) => {
  try {
    const url = new URL(req.url, "http://gateway.local");
    if (url.pathname === "/device") {
      const installationId = String(url.searchParams.get("installationId") || "");
      if (!/^[A-Za-z0-9._-]{2,80}$/.test(installationId)) throw Object.assign(new Error("Installation invalide"), { status: 400 });
      const decoded = await verifyIdToken(bearer(req));
      if (!(await deviceCanConnect(decoded, installationId))) throw Object.assign(new Error("Équipement non autorisé"), { status: 403 });
      wss.handleUpgrade(req, socket, head, (ws) => {
        ws.kind = "device";
        ws.installationId = installationId;
        ws.isAlive = true;
        const previous = devices.get(installationId);
        if (previous && previous.readyState === WebSocket.OPEN) previous.close(4001, "Connexion remplacée");
        devices.set(installationId, ws);
        wss.emit("connection", ws, req);
      });
      return;
    }
    if (url.pathname === "/client") {
      if (!originAllowed(req.headers.origin)) throw Object.assign(new Error("Origine refusée"), { status: 403 });
      const ticketValue = String(url.searchParams.get("ticket") || "");
      const session = tickets.get(ticketValue);
      tickets.delete(ticketValue);
      if (!session || session.expiresAt < Date.now()) throw Object.assign(new Error("Session expirée"), { status: 401 });
      const device = devices.get(session.installationId);
      if (!device || device.readyState !== WebSocket.OPEN) throw Object.assign(new Error("EtR hors ligne"), { status: 409 });
      wss.handleUpgrade(req, socket, head, (ws) => {
        ws.kind = "client";
        ws.installationId = session.installationId;
        ws.device = device;
        ws.isAlive = true;
        if (device.viewer && device.viewer.readyState === WebSocket.OPEN) device.viewer.close(4002, "Nouvelle session");
        device.viewer = ws;
        wss.emit("connection", ws, req);
      });
      return;
    }
    socket.destroy();
  } catch (error) {
    const status = Number(error.status || 500);
    socket.write(`HTTP/1.1 ${status} Error\r\nConnection: close\r\n\r\n`);
    socket.destroy();
  }
});

wss.on("connection", (ws) => {
  ws.on("pong", () => { ws.isAlive = true; });
  if (ws.kind === "client") {
    ws.device.send(JSON.stringify({ type: "open" }));
  }

  ws.on("message", (data, isBinary) => {
    if (ws.kind === "device") {
      const viewer = ws.viewer;
      if (isBinary && viewer && viewer.readyState === WebSocket.OPEN) viewer.send(data, { binary: true });
      return;
    }
    if (ws.kind === "client" && isBinary && ws.device?.readyState === WebSocket.OPEN) {
      ws.device.send(data, { binary: true });
    }
  });

  ws.on("close", () => {
    if (ws.kind === "device") {
      if (devices.get(ws.installationId) === ws) devices.delete(ws.installationId);
      if (ws.viewer?.readyState === WebSocket.OPEN) ws.viewer.close(4003, "EtR déconnecté");
    } else if (ws.kind === "client" && ws.device?.readyState === WebSocket.OPEN) {
      if (ws.device.viewer === ws) ws.device.viewer = null;
      ws.device.send(JSON.stringify({ type: "close" }));
    }
  });
});

setInterval(() => {
  const now = Date.now();
  for (const [ticket, value] of tickets) if (value.expiresAt < now) tickets.delete(ticket);
  for (const ws of wss.clients) {
    if (ws.isAlive === false) { ws.terminate(); continue; }
    ws.isAlive = false;
    ws.ping();
  }
}, 30_000).unref();

server.listen(PORT, "0.0.0.0", () => {
  console.log(`EtR remote gateway listening on ${PORT}`);
});
