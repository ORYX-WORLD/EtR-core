import crypto from "node:crypto";
import { EnrollmentError } from "./enrollment.mjs";

function safeFirebaseError(status) {
  if (status === 400) return new EnrollmentError(503, "device_session_refused", "Session technique Firebase indisponible");
  return new EnrollmentError(503, "device_session_unavailable", "Service d’identité technique indisponible");
}

function technicalEmail(deviceUid) {
  const normalized = String(deviceUid || "").replace(/[^A-Za-z0-9._-]/g, "").slice(0, 100);
  if (!normalized) throw new Error("invalid_device_uid");
  return `${normalized}@devices.oryx.invalid`;
}

function randomPassword(randomBytes) {
  // 384 random bits plus explicit character classes for Firebase password rules.
  return `${randomBytes(48).toString("base64url")}aA1!`;
}

export function createFirebasePasswordSessionIssuer({
  auth,
  apiKey,
  fetchImpl = globalThis.fetch,
  randomBytes = crypto.randomBytes
} = {}) {
  if (!auth?.getUser || !auth?.createUser || !auth?.updateUser || !auth?.setCustomUserClaims || !auth?.deleteUser) {
    throw new Error("Firebase password session issuer requires Firebase Admin Auth");
  }
  if (typeof fetchImpl !== "function") throw new Error("Firebase password session issuer requires fetch");
  const webApiKey = String(apiKey || "").trim();
  if (webApiKey.length < 20) throw new Error("FIREBASE_WEB_API_KEY is not configured");

  async function signIn(email, password) {
    let response;
    try {
      response = await fetchImpl(
        `https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${encodeURIComponent(webApiKey)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ email, password, returnSecureToken: true }),
          signal: AbortSignal.timeout(15_000)
        }
      );
    } catch (error) {
      throw new EnrollmentError(503, "device_session_unavailable", "Service d’identité technique indisponible", { cause: error });
    }
    const text = await response.text();
    let payload = {};
    try { payload = JSON.parse(text); } catch {}
    if (!response.ok) throw safeFirebaseError(response.status);
    const idToken = String(payload.idToken || "");
    const refreshToken = String(payload.refreshToken || "");
    if (idToken.split(".").length !== 3 || refreshToken.length < 40) {
      throw new EnrollmentError(503, "device_session_invalid", "Session technique Firebase invalide");
    }
    return {
      idToken,
      refreshToken,
      expiresIn: Math.max(60, Number(payload.expiresIn || 3600))
    };
  }

  async function ensureTechnicalUser(deviceUid, installationId, password) {
    const email = technicalEmail(deviceUid);
    const userData = {
      email,
      password,
      emailVerified: true,
      disabled: false,
      displayName: `EtR ${String(installationId).slice(-12).toUpperCase()}`
    };
    try {
      await auth.getUser(deviceUid);
      await auth.updateUser(deviceUid, userData);
    } catch (error) {
      if (error?.code !== "auth/user-not-found") throw error;
      await auth.createUser({ uid: deviceUid, ...userData });
    }
    await auth.setCustomUserClaims(deviceUid, {
      etrDevice: true,
      installationId: String(installationId)
    });
    return email;
  }

  async function issue({ deviceUid, installationId }) {
    const password = randomPassword(randomBytes);
    const email = await ensureTechnicalUser(deviceUid, installationId, password);
    const session = await signIn(email, password);
    return {
      ...session,
      deviceUid,
      installationId,
      authenticationMethod: "server_generated_password_session"
    };
  }

  async function probe(runId = "manual") {
    const suffix = crypto.createHash("sha256").update(String(runId)).digest("hex").slice(0, 28);
    const deviceUid = `etrprobe_${suffix}`;
    let created = false;
    try {
      const session = await issue({ deviceUid, installationId: `etr-probe-${suffix.slice(0, 12)}` });
      created = true;
      return {
        ok: session.idToken.split(".").length === 3 && session.refreshToken.length >= 40,
        issuer: "firebase-password-session",
        credentialsReturned: false
      };
    } finally {
      try {
        await auth.deleteUser(deviceUid);
      } catch (error) {
        if (created || error?.code !== "auth/user-not-found") {
          console.warn("Technical session probe cleanup failed", { code: error?.code || "", message: String(error?.message || "").slice(0, 160) });
        }
      }
    }
  }

  return { issue, probe };
}
