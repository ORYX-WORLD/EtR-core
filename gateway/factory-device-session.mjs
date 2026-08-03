import crypto from "node:crypto";
import { normalizeSerial, serialFingerprint } from "./enrollment.mjs";

const SIGN_UP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signUp";
const SIGN_IN_URL = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword";

function sessionError(status, detail = "") {
  const error = new Error(`factory_device_session_${status}${detail ? `_${detail}` : ""}`);
  error.code = "auth/factory-device-session-unavailable";
  error.status = 502;
  return error;
}

function credentials(ticket, serial) {
  const normalizedTicket = String(ticket || "").trim();
  if (!/^[A-Za-z0-9_-]{40,120}$/.test(normalizedTicket)) {
    const error = new Error("factory_ticket_invalid");
    error.code = "factory_ticket_invalid";
    error.status = 400;
    throw error;
  }
  const normalizedSerial = normalizeSerial(serial);
  const serialHash = serialFingerprint(normalizedSerial);
  const ticketHash = crypto.createHash("sha256").update(normalizedTicket, "utf8").digest("hex");
  const password = `${crypto
    .createHmac("sha256", Buffer.from(normalizedTicket, "utf8"))
    .update(`etr-factory-device:${serialHash}`, "utf8")
    .digest("base64url")}Aa1!`;
  return {
    email: `etrdev_${ticketHash.slice(0, 40)}@devices.oryx.invalid`,
    password,
    serialHash
  };
}

async function postIdentityToolkit({ url, apiKey, payload, fetchImpl }) {
  const target = new URL(url);
  target.searchParams.set("key", apiKey);
  const response = await fetchImpl(target, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(payload),
    redirect: "error",
    signal: AbortSignal.timeout(15_000)
  });
  let body = {};
  try {
    body = await response.json();
  } catch {
    body = {};
  }
  return { response, body };
}

function normalizeSession(body) {
  const uid = String(body.localId || "").trim();
  const idToken = String(body.idToken || "").trim();
  const refreshToken = String(body.refreshToken || "").trim();
  const expiresIn = Math.max(60, Number(body.expiresIn || 3600));
  if (!uid || uid.length > 128 || idToken.split(".").length !== 3 || refreshToken.length < 40) {
    throw sessionError(502, "invalid_payload");
  }
  return {
    uid,
    idToken,
    refreshToken,
    expiresIn,
    authMode: "factory_password_session"
  };
}

export function createFactoryDeviceSessionIssuer({
  apiKey = process.env.FIREBASE_API_KEY,
  fetchImpl = globalThis.fetch
} = {}) {
  const normalizedApiKey = String(apiKey || "").trim();

  async function issue({ ticket, serial } = {}) {
    if (normalizedApiKey.length < 20 || normalizedApiKey.length > 200 || typeof fetchImpl !== "function") {
      throw sessionError(503, "not_configured");
    }
    const identity = credentials(ticket, serial);
    const common = {
      email: identity.email,
      password: identity.password,
      returnSecureToken: true
    };
    let { response, body } = await postIdentityToolkit({
      url: SIGN_UP_URL,
      apiKey: normalizedApiKey,
      payload: common,
      fetchImpl
    });
    const firebaseReason = String(body?.error?.message || "").split(" : ", 1)[0];
    if (!response.ok && firebaseReason === "EMAIL_EXISTS") {
      ({ response, body } = await postIdentityToolkit({
        url: SIGN_IN_URL,
        apiKey: normalizedApiKey,
        payload: common,
        fetchImpl
      }));
    }
    if (!response.ok) {
      const reason = String(body?.error?.message || response.status || "unknown").slice(0, 80);
      throw sessionError(response.status || 502, reason);
    }
    return {
      ...normalizeSession(body),
      serialHash: identity.serialHash
    };
  }

  return { issue };
}

export const FACTORY_DEVICE_SESSION_POLICY = Object.freeze({
  mode: "factory_password_session",
  passwordEntropyBits: 256,
  accountDomain: "devices.oryx.invalid",
  adminAuthRequired: false,
  customClaimsRequired: false
});
