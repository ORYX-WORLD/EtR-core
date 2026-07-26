import crypto from "node:crypto";

const FIREBASE_SIGN_IN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword";

function internalDeviceEmail(uid) {
  if (!/^etr(?:dev|health)_[a-z0-9_]{8,64}$/i.test(String(uid || ""))) {
    throw new Error("invalid_device_uid");
  }
  return `${String(uid).toLowerCase()}@devices.oryx.invalid`;
}

function safeFirebaseError(status) {
  const error = new Error(`firebase_device_session_${status}`);
  error.code = "auth/device-session-unavailable";
  return error;
}

export function createFirebaseDeviceSessionIssuer({
  auth,
  apiKey = process.env.FIREBASE_API_KEY,
  fetchImpl = globalThis.fetch,
  randomBytes = crypto.randomBytes
}) {
  if (!auth || typeof auth.getUser !== "function" || typeof auth.updateUser !== "function") {
    throw new Error("Firebase device session issuer requires Admin Auth");
  }
  if (typeof fetchImpl !== "function") throw new Error("Firebase device session issuer requires fetch");
  const normalizedApiKey = String(apiKey || "").trim();
  if (normalizedApiKey.length < 20 || normalizedApiKey.length > 200) {
    throw new Error("FIREBASE_API_KEY absente ou invalide");
  }

  async function ensureDeviceUser(uid, claims, password) {
    const email = internalDeviceEmail(uid);
    const profile = {
      email,
      emailVerified: true,
      password,
      disabled: false,
      displayName: String(claims?.installationId || uid).slice(0, 128)
    };
    try {
      await auth.getUser(uid);
      if (typeof auth.revokeRefreshTokens === "function") await auth.revokeRefreshTokens(uid);
      await auth.updateUser(uid, profile);
    } catch (error) {
      if (error?.code !== "auth/user-not-found") throw error;
      await auth.createUser({ uid, ...profile });
    }
    await auth.setCustomUserClaims(uid, {
      etrDevice: true,
      installationId: String(claims?.installationId || "").slice(0, 80)
    });
    return email;
  }

  async function signIn(email, password) {
    const response = await fetchImpl(FIREBASE_SIGN_IN_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ email, password, returnSecureToken: true }),
      redirect: "error"
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      payload = {};
    }
    if (!response.ok) throw safeFirebaseError(response.status);
    const idToken = String(payload.idToken || "");
    const refreshToken = String(payload.refreshToken || "");
    const expiresIn = Math.max(60, Number(payload.expiresIn || 3600));
    if (idToken.split(".").length !== 3 || refreshToken.length < 40) {
      throw safeFirebaseError(502);
    }
    return { idToken, refreshToken, expiresIn, authMode: "password_session" };
  }

  async function issue(uid, claims = {}) {
    // This password exists only in this request scope. It is immediately rotated
    // on the next issuance and is never returned to the Raspberry or logged.
    const password = randomBytes(48).toString("base64url");
    const email = await ensureDeviceUser(uid, claims, password);
    return signIn(email, password);
  }

  async function health(runId) {
    const uid = `etrhealth_${crypto.createHash("sha256").update(String(runId || "health")).digest("hex").slice(0, 32)}`;
    try {
      const session = await issue(uid, { installationId: "etr-signing-health" });
      if (!session.idToken || !session.refreshToken) throw safeFirebaseError(502);
      return { ok: true, mode: "firebase-password-session", tokenExchange: true };
    } finally {
      try {
        if (typeof auth.revokeRefreshTokens === "function") await auth.revokeRefreshTokens(uid);
        if (typeof auth.deleteUser === "function") await auth.deleteUser(uid);
      } catch {
        // The health identity is disposable; cleanup failure must not expose internals.
      }
    }
  }

  return { issue, health };
}

export const FIREBASE_DEVICE_SESSION_POLICY = Object.freeze({
  mode: "password_session",
  passwordEntropyBits: 384,
  internalDomain: "devices.oryx.invalid"
});
