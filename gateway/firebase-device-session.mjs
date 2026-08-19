import crypto from "node:crypto";

const FIREBASE_SIGN_UP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signUp";
const FIREBASE_DELETE_URL = "https://identitytoolkit.googleapis.com/v1/accounts:delete";

function validateDeviceUidHint(uid) {
  if (!/^etr(?:dev|health)_[a-z0-9_]{8,64}$/i.test(String(uid || ""))) {
    throw new Error("invalid_device_uid");
  }
}

function safeFirebaseError(status) {
  const error = new Error(`firebase_device_session_${status}`);
  error.code = "auth/device-session-unavailable";
  error.status = 502;
  return error;
}

async function postIdentityToolkit({ url, apiKey, payload, fetchImpl }) {
  const target = new URL(url);
  target.searchParams.set("key", apiKey);
  let response;
  try {
    response = await fetchImpl(target, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload),
      redirect: "error",
      signal: AbortSignal.timeout(15_000)
    });
  } catch {
    throw safeFirebaseError(503);
  }

  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  if (!response.ok) throw safeFirebaseError(response.status || 502);
  return body;
}

function normalizeSession(body) {
  const uid = String(body.localId || "").trim();
  const idToken = String(body.idToken || "").trim();
  const refreshToken = String(body.refreshToken || "").trim();
  const expiresIn = Math.max(60, Number(body.expiresIn || 3600));
  if (!uid || uid.length > 128 || idToken.split(".").length !== 3 || refreshToken.length < 40) {
    throw safeFirebaseError(502);
  }
  return { uid, idToken, refreshToken, expiresIn, authMode: "password_session" };
}

export function createFirebaseDeviceSessionIssuer({
  apiKey = process.env.FIREBASE_API_KEY,
  fetchImpl = globalThis.fetch,
  randomBytes = crypto.randomBytes
} = {}) {
  if (typeof fetchImpl !== "function") throw new Error("Firebase device session issuer requires fetch");
  const normalizedApiKey = String(apiKey || "").trim();
  if (normalizedApiKey.length < 20 || normalizedApiKey.length > 200) {
    throw new Error("FIREBASE_API_KEY absente ou invalide");
  }

  async function issue(uidHint, claims = {}) {
    validateDeviceUidHint(uidHint);
    const installationId = String(claims?.installationId || "").trim();
    if (!/^etr-[a-z0-9-]{4,76}$/i.test(installationId)) throw new Error("invalid_installation_id");

    const alias = randomBytes(24).toString("hex");
    const password = `${randomBytes(48).toString("base64url")}Aa1!`;
    const email = `etrdev_${alias}@devices.oryx.invalid`;
    const body = await postIdentityToolkit({
      url: FIREBASE_SIGN_UP_URL,
      apiKey: normalizedApiKey,
      payload: { email, password, returnSecureToken: true },
      fetchImpl
    });
    return normalizeSession(body);
  }

  async function revoke(session) {
    const idToken = String(session?.idToken || "").trim();
    if (idToken.split(".").length !== 3) throw safeFirebaseError(400);
    await postIdentityToolkit({
      url: FIREBASE_DELETE_URL,
      apiKey: normalizedApiKey,
      payload: { idToken },
      fetchImpl
    });
  }

  async function health(runId) {
    const uidHint = `etrhealth_${crypto.createHash("sha256").update(String(runId || "health")).digest("hex").slice(0, 32)}`;
    const session = await issue(uidHint, { installationId: "etr-signing-health" });
    await revoke(session);
    return { ok: true, mode: "firebase-password-session", tokenExchange: true };
  }

  return { issue, health, revoke, managesUsers: false };
}

export const FIREBASE_DEVICE_SESSION_POLICY = Object.freeze({
  mode: "password_session",
  passwordEntropyBits: 384,
  internalDomain: "devices.oryx.invalid",
  adminAuthRequired: false,
  customClaimsRequired: false,
  firebaseUidSource: "identity-toolkit-sign-up"
});

// Explicitly document that the deployable path does not use the former Admin
// Auth operations. These policy keys also make future regressions searchable.
export const FIREBASE_DEVICE_SESSION_LEGACY_ADMIN_OPERATIONS = Object.freeze({
  signInWithPassword: false,
  setCustomUserClaims: false,
  revokeRefreshTokens: false
});
