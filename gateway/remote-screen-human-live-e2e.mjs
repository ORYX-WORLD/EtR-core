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

function safeRemoteError(prefix, status, body = {}) {
  const code = String(body?.error?.message || body?.error?.status || body?.code || "unknown")
    .replace(/[^A-Za-z0-9._:-]/g, "_")
    .slice(0, 160);
  return new Error(`${prefix}_http_${status}:${code}`);
}

function decodeJwtPayload(token) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) throw new Error("firebase_id_token_invalid");
  return JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
}

async function firebasePublicRequest({ apiKey, method, body }) {
  const response = await fetch(
    `https://identitytoolkit.googleapis.com/v1/${method}?key=${encodeURIComponent(apiKey)}`,
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        accept: "application/json",
        "user-agent": "EtR-Human-Remote-E2E/2.0"
      },
      body: JSON.stringify(body),
      cache: "no-store"
    }
  );
  const payload = await response.json().catch(() => ({}));
  return { response, payload };
}

async function signUpWithPassword({ apiKey, email, password }) {
  const { response, payload } = await firebasePublicRequest({
    apiKey,
    method: "accounts:signUp",
    body: { email, password, returnSecureToken: true }
  });
  if (
    !response.ok ||
    typeof payload.localId !== "string" ||
    typeof payload.idToken !== "string" ||
    typeof payload.refreshToken !== "string"
  ) {
    throw safeRemoteError("firebase_sign_up", response.status, payload);
  }
  return {
    uid: payload.localId,
    idToken: payload.idToken,
    refreshToken: payload.refreshToken
  };
}

async function createVerificationCode({ projectId, oauthAccessToken, email }) {
  const response = await fetch(
    `https://identitytoolkit.googleapis.com/v1/projects/${encodeURIComponent(projectId)}/accounts:sendOobCode`,
    {
      method: "POST",
      headers: {
        authorization: `Bearer ${oauthAccessToken}`,
        "content-type": "application/json",
        accept: "application/json",
        "user-agent": "EtR-Human-Remote-E2E/2.0"
      },
      body: JSON.stringify({
        requestType: "VERIFY_EMAIL",
        email,
        returnOobLink: true
      }),
      cache: "no-store"
    }
  );
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw safeRemoteError("firebase_verification_link", response.status, payload);
  }
  let oobCode = String(payload.oobCode || "").trim();
  if (!oobCode && typeof payload.oobLink === "string") {
    try {
      oobCode = String(new URL(payload.oobLink).searchParams.get("oobCode") || "").trim();
    } catch {
      oobCode = "";
    }
  }
  if (oobCode.length < 8 || oobCode.length > 2048) {
    throw new Error("firebase_verification_code_missing");
  }
  return oobCode;
}

async function applyVerificationCode({ apiKey, oobCode }) {
  const { response, payload } = await firebasePublicRequest({
    apiKey,
    method: "accounts:update",
    body: { oobCode }
  });
  if (!response.ok || payload.emailVerified !== true) {
    throw safeRemoteError("firebase_verify_email", response.status, payload);
  }
}

async function signInWithPassword({ apiKey, email, password }) {
  const { response, payload } = await firebasePublicRequest({
    apiKey,
    method: "accounts:signInWithPassword",
    body: { email, password, returnSecureToken: true }
  });
  if (!response.ok || typeof payload.idToken !== "string") {
    throw safeRemoteError("firebase_sign_in", response.status, payload);
  }
  return payload.idToken;
}

async function deleteCurrentUser({ apiKey, idToken }) {
  if (!idToken) return false;
  const { response } = await firebasePublicRequest({
    apiKey,
    method: "accounts:delete",
    body: { idToken }
  });
  return response.ok;
}

async function userCanStillSignIn({ apiKey, email, password }) {
  const { response } = await firebasePublicRequest({
    apiKey,
    method: "accounts:signInWithPassword",
    body: { email, password, returnSecureToken: true }
  });
  return response.ok;
}

async function requestHumanRemoteSession({ gatewayOrigin, idToken, installationId, allowedOrigin }) {
  const response = await fetch(`${gatewayOrigin}/api/remote-session`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${idToken}`,
      "content-type": "application/json",
      accept: "application/json",
      origin: allowedOrigin,
      "user-agent": "EtR-Human-Remote-E2E/2.0"
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
      "user-agent": "EtR-Human-Remote-E2E/2.0"
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
      "user-agent": "EtR-Human-Remote-E2E/2.0"
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
    let settled = false;
    const finish = (callback) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      callback();
    };
    const timeout = setTimeout(() => {
      websocket.terminate();
      finish(() => reject(new Error("vnc_banner_timeout")));
    }, 25_000);

    websocket.once("open", () => {
      opened = true;
    });
    websocket.on("message", (data, isBinary) => {
      if (!isBinary || settled) return;
      const bytes = Buffer.from(data);
      const prefix = bytes.subarray(0, 12).toString("ascii");
      if (!prefix.startsWith("RFB ")) {
        websocket.close(1000, "unexpected banner");
        finish(() => reject(new Error(`vnc_banner_invalid:${JSON.stringify(prefix)}`)));
        return;
      }
      websocket.close(1000, "human e2e complete");
      finish(() => resolve({
        websocketOpened: opened,
        websocketStatus: 101,
        vncBanner: prefix.trim(),
        firstPayloadBytes: bytes.length
      }));
    });
    websocket.once("unexpected-response", (_request, response) => {
      finish(() => reject(new Error(`viewer_websocket_http_${response.statusCode || 0}`)));
    });
    websocket.once("error", error => {
      finish(() => reject(error));
    });
  });
}

async function verifyCleanup({ membershipRef, apiKey, email, password, userDeletionRequested }) {
  const membership = membershipRef ? await membershipRef.get() : null;
  const userStillSignsIn = userDeletionRequested
    ? await userCanStillSignIn({ apiKey, email, password }).catch(() => false)
    : true;
  return {
    membershipDeleted: Boolean(membershipRef && !membership.exists()),
    userDeleted: Boolean(userDeletionRequested && !userStillSignsIn)
  };
}

async function main() {
  const projectId = requiredEnvironment("FIREBASE_PROJECT_ID");
  const databaseURL = requiredEnvironment("FIREBASE_DATABASE_URL");
  const apiKey = requiredEnvironment("FIREBASE_API_KEY");
  const oauthAccessToken = requiredEnvironment("GOOGLE_OAUTH_ACCESS_TOKEN");
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
  const db = app.database();
  let uid = "";
  let currentIdToken = "";
  let membershipRef = null;
  let userDeletionRequested = false;
  let testResult = null;
  let primaryError = null;
  let primaryStage = "initialize";
  let cleanup = { membershipDeleted: false, userDeleted: false };

  try {
    primaryStage = "public-sign-up";
    const signUp = await signUpWithPassword({ apiKey, email, password });
    uid = signUp.uid;
    currentIdToken = signUp.idToken;

    primaryStage = "verification-link";
    const oobCode = await createVerificationCode({
      projectId,
      oauthAccessToken,
      email
    });

    primaryStage = "verify-email";
    await applyVerificationCode({ apiKey, oobCode });

    primaryStage = "create-membership";
    membershipRef = db.ref(`memberships/${uid}/${installationId}`);
    await membershipRef.set({
      active: true,
      role: "viewer",
      source: "github-actions-human-e2e",
      createdAt: admin.database.ServerValue.TIMESTAMP
    });

    primaryStage = "verified-sign-in";
    currentIdToken = await signInWithPassword({ apiKey, email, password });
    const tokenPayload = decodeJwtPayload(currentIdToken);
    if (tokenPayload.sub !== uid || tokenPayload.email_verified !== true) {
      throw new Error("verified_human_token_claims_invalid");
    }

    primaryStage = "remote-session";
    const remoteSession = await requestHumanRemoteSession({
      gatewayOrigin,
      idToken: currentIdToken,
      installationId,
      allowedOrigin
    });

    primaryStage = "viewer-assets";
    const assets = await verifyViewerAssets(remoteSession.body.viewerUrl, gatewayOrigin);

    primaryStage = "viewer-websocket";
    const websocket = await verifyVncBanner({
      gatewayOrigin,
      ticket: assets.ticket,
      allowedOrigin
    });

    testResult = {
      ok: true,
      temporaryUserCreatedByPublicApi: true,
      verificationCodeReturnedWithoutEmail: true,
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
    if (currentIdToken) {
      try {
        userDeletionRequested = await deleteCurrentUser({ apiKey, idToken: currentIdToken });
      } catch {}
    }
    if (uid) {
      cleanup = await verifyCleanup({
        membershipRef,
        apiKey,
        email,
        password,
        userDeletionRequested
      }).catch(() => cleanup);
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
    stage: primaryStage,
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
    stage: "bootstrap",
    error: error instanceof Error ? error.name : "Error",
    message: String(error?.message || error || "unknown").slice(0, 800)
  };
  process.stderr.write(`${JSON.stringify(report, null, 2)}\n`);
  process.exitCode = 1;
});
