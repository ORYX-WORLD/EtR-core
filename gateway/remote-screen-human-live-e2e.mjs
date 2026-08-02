import crypto from "node:crypto";
import { writeFile } from "node:fs/promises";
import process from "node:process";
import admin from "firebase-admin";
import { WebSocket } from "ws";

function requiredEnvironment(name) {
  const value = String(process.env[name] || "").trim();
  if (!value) throw new Error(`${name}_missing`);
  return value;
}

function decodeJwtPayload(token) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) throw new Error("firebase_id_token_invalid");
  return JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
}

async function signInWithPassword({ apiKey, email, password }) {
  const response = await fetch(
    `https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${encodeURIComponent(apiKey)}`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        "user-agent": "EtR-Human-Remote-E2E/1.0"
      },
      body: JSON.stringify({ email, password, returnSecureToken: true }),
      cache: "no-store"
    }
  );
  const body = await response.json().catch(() => ({}));
  if (!response.ok || typeof body.idToken !== "string") {
    throw new Error(`firebase_sign_in_http_${response.status}:${String(body?.error?.message || "unknown")}`);
  }
  return body.idToken;
}

async function requestHumanRemoteSession({ gatewayOrigin, idToken, installationId, allowedOrigin }) {
  const response = await fetch(`${gatewayOrigin}/api/remote-session`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${idToken}`,
      "content-type": "application/json",
      accept: "application/json",
      origin: allowedOrigin,
      "user-agent": "EtR-Human-Remote-E2E/1.0"
    },
    body: JSON.stringify({ installationId }),
    cache: "no-store"
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(`remote_session_http_${response.status}:${String(body.code || body.error || "unknown")}`);
  }
  if (typeof body.viewerUrl !== "string" || Number(body.expiresIn) <= 0) {
    throw new Error("remote_session_payload_invalid");
  }
  return { status: response.status, body };
}

async function verifyViewerAssets(viewerUrl, expectedOrigin) {
  const parsed = new URL(viewerUrl);
  if (
    parsed.protocol !== "https:" ||
    parsed.origin !== expectedOrigin ||
    parsed.pathname !== "/viewer" ||
    !parsed.searchParams.get("ticket")
  ) {
    throw new Error("viewer_url_invalid");
  }

  const response = await fetch(parsed, {
    headers: {
      accept: "text/html",
      "user-agent": "EtR-Human-Remote-E2E/1.0"
    },
    cache: "no-store"
  });
  const html = await response.text();
  if (!response.ok) throw new Error(`viewer_http_${response.status}`);
  if (!/no-store/i.test(response.headers.get("cache-control") || "")) {
    throw new Error("viewer_cache_policy_invalid");
  }
  for (const marker of [
    'import RFB from "/novnc/rfb-browser-v2.js"',
    'new RFB(document.getElementById("screen"),url',
    "Connexion sécurisée à l’EtR"
  ]) {
    if (!html.includes(marker)) throw new Error(`viewer_marker_missing:${marker}`);
  }

  const bundle = await fetch(new URL("/novnc/rfb-browser-v2.js", parsed.origin), {
    headers: {
      accept: "text/javascript",
      "user-agent": "EtR-Human-Remote-E2E/1.0"
    }
  });
  const bytes = Buffer.from(await bundle.arrayBuffer());
  if (!bundle.ok || bytes.length < 10_000) {
    throw new Error(`novnc_bundle_invalid:${bundle.status}:${bytes.length}`);
  }
  return {
    viewerStatus: response.status,
    viewerCacheControl: response.headers.get("cache-control") || "",
    noVncBundleStatus: bundle.status,
    noVncBundleBytes: bytes.length,
    ticket: parsed.searchParams.get("ticket")
  };
}

async function verifyVncBanner({ gatewayOrigin, ticket, allowedOrigin }) {
  const websocketUrl = new URL(gatewayOrigin);
  websocketUrl.protocol = "wss:";
  websocketUrl.pathname = "/client";
  websocketUrl.search = new URLSearchParams({ ticket }).toString();

  return await new Promise((resolve, reject) => {
    const websocket = new WebSocket(websocketUrl, { origin: allowedOrigin });
    let opened = false;
    const timeout = setTimeout(() => {
      websocket.terminate();
      reject(new Error("vnc_banner_timeout"));
    }, 25_000);

    websocket.once("open", () => {
      opened = true;
    });
    websocket.on("message", (data, isBinary) => {
      if (!isBinary) return;
      const bytes = Buffer.from(data);
      const prefix = bytes.subarray(0, 12).toString("ascii");
      if (!prefix.startsWith("RFB ")) {
        clearTimeout(timeout);
        websocket.close(1000, "unexpected banner");
        reject(new Error(`vnc_banner_invalid:${JSON.stringify(prefix)}`));
        return;
      }
      clearTimeout(timeout);
      websocket.close(1000, "human e2e complete");
      resolve({
        websocketOpened: opened,
        websocketStatus: 101,
        vncBanner: prefix.trim(),
        firstPayloadBytes: bytes.length
      });
    });
    websocket.once("unexpected-response", (_request, response) => {
      clearTimeout(timeout);
      reject(new Error(`viewer_websocket_http_${response.statusCode || 0}`));
    });
    websocket.once("error", error => {
      clearTimeout(timeout);
      reject(error);
    });
  });
}

async function verifyCleanup({ auth, membershipRef, uid }) {
  const membership = await membershipRef.get();
  let userDeleted = false;
  try {
    await auth.getUser(uid);
  } catch (error) {
    userDeleted = error?.code === "auth/user-not-found";
  }
  return {
    membershipDeleted: !membership.exists(),
    userDeleted
  };
}

async function main() {
  const projectId = requiredEnvironment("FIREBASE_PROJECT_ID");
  const databaseURL = requiredEnvironment("FIREBASE_DATABASE_URL");
  const apiKey = requiredEnvironment("FIREBASE_API_KEY");
  const gatewayOrigin = new URL(requiredEnvironment("GATEWAY_URL")).origin;
  const installationId = String(process.env.ETR_INSTALLATION_ID || "etr-core").trim();
  const allowedOrigin = String(
    process.env.ETR_VIEWER_ORIGIN || "https://oryx-froid-industriel.web.app"
  ).trim();
  const outputPath = String(process.env.ETR_HUMAN_E2E_OUTPUT || "").trim();
  const runId = String(process.env.GITHUB_RUN_ID || Date.now()).replace(/[^0-9]/g, "");
  const randomSuffix = crypto.randomBytes(8).toString("hex");
  const email = `etr-e2e-${runId}-${randomSuffix}@devices.oryx.invalid`;
  const password = `EtR!${crypto.randomBytes(30).toString("base64url")}Aa1`;

  const app = admin.initializeApp(
    {
      credential: admin.credential.applicationDefault(),
      databaseURL,
      projectId
    },
    `etr-human-e2e-${randomSuffix}`
  );
  const auth = app.auth();
  const db = app.database();
  let uid = "";
  let membershipRef = null;
  let testResult = null;
  let primaryError = null;
  let cleanup = { membershipDeleted: false, userDeleted: false };

  try {
    const user = await auth.createUser({
      email,
      password,
      emailVerified: true,
      disabled: false,
      displayName: "EtR temporary human E2E"
    });
    uid = user.uid;
    membershipRef = db.ref(`memberships/${uid}/${installationId}`);
    await membershipRef.set({
      active: true,
      role: "viewer",
      source: "github-actions-human-e2e",
      createdAt: admin.database.ServerValue.TIMESTAMP
    });

    const idToken = await signInWithPassword({ apiKey, email, password });
    const tokenPayload = decodeJwtPayload(idToken);
    if (tokenPayload.sub !== uid || tokenPayload.email_verified !== true) {
      throw new Error("verified_human_token_claims_invalid");
    }

    const remoteSession = await requestHumanRemoteSession({
      gatewayOrigin,
      idToken,
      installationId,
      allowedOrigin
    });
    const assets = await verifyViewerAssets(remoteSession.body.viewerUrl, gatewayOrigin);
    const websocket = await verifyVncBanner({
      gatewayOrigin,
      ticket: assets.ticket,
      allowedOrigin
    });

    testResult = {
      ok: true,
      temporaryVerifiedUserCreated: true,
      membershipCreated: true,
      emailVerifiedClaim: true,
      remoteSessionStatus: remoteSession.status,
      viewerStatus: assets.viewerStatus,
      viewerCacheControl: assets.viewerCacheControl,
      noVncBundleStatus: assets.noVncBundleStatus,
      noVncBundleBytes: assets.noVncBundleBytes,
      websocketOpened: websocket.websocketOpened,
      websocketStatus: websocket.websocketStatus,
      vncBanner: websocket.vncBanner,
      firstPayloadBytes: websocket.firstPayloadBytes
    };
  } catch (error) {
    primaryError = error;
  } finally {
    if (membershipRef) {
      try {
        await membershipRef.remove();
      } catch {}
    }
    if (uid) {
      try {
        await auth.deleteUser(uid);
      } catch {}
    }
    if (uid && membershipRef) {
      cleanup = await verifyCleanup({ auth, membershipRef, uid }).catch(() => cleanup);
    }
    await app.delete().catch(() => undefined);
  }

  const ok = Boolean(testResult?.ok && cleanup.membershipDeleted && cleanup.userDeleted && !primaryError);
  const report = {
    ok,
    checkedAt: new Date().toISOString(),
    projectId,
    gatewayOrigin,
    installationId,
    result: testResult,
    cleanup,
    error: primaryError ? primaryError.name || "Error" : null,
    message: primaryError ? String(primaryError.message || primaryError).slice(0, 800) : ""
  };
  const serialized = `${JSON.stringify(report, null, 2)}\n`;
  if (outputPath) await writeFile(outputPath, serialized, "utf8");
  process.stdout.write(serialized);
  if (!ok) process.exitCode = 1;
}

main().catch(error => {
  const report = {
    ok: false,
    checkedAt: new Date().toISOString(),
    error: error instanceof Error ? error.name : "Error",
    message: String(error?.message || error || "unknown").slice(0, 800)
  };
  process.stderr.write(`${JSON.stringify(report, null, 2)}\n`);
  process.exitCode = 1;
});
