import { decodeJwt, decodeProtectedHeader } from "jose";

function gatewayError(message, status, code, cause) {
  return Object.assign(new Error(message, { cause }), { status, code });
}

export function decodeFirebaseDeviceIdentity(
  token,
  {
    projectId,
    now = Math.floor(Date.now() / 1000)
  } = {}
) {
  if (!token) {
    throw gatewayError(
      "Jeton appareil manquant",
      401,
      "device-access/token-missing"
    );
  }
  if (!projectId) {
    throw gatewayError(
      "Projet Firebase appareil non configuré",
      503,
      "device-access/project-missing"
    );
  }

  let header;
  let payload;
  try {
    header = decodeProtectedHeader(token);
    payload = decodeJwt(token);
  } catch (error) {
    throw gatewayError(
      "Jeton appareil invalide",
      401,
      "device-access/token-invalid",
      error
    );
  }

  const issuer = `https://securetoken.google.com/${projectId}`;
  if (header.alg !== "RS256") {
    throw gatewayError(
      "Algorithme du jeton appareil invalide",
      401,
      "device-access/token-invalid"
    );
  }
  if (payload.aud !== projectId || payload.iss !== issuer) {
    throw gatewayError(
      "Projet du jeton appareil invalide",
      401,
      "device-access/token-project-invalid"
    );
  }
  if (typeof payload.sub !== "string" || payload.sub.length === 0 || payload.sub.length > 128) {
    throw gatewayError(
      "UID appareil invalide",
      401,
      "device-access/uid-invalid"
    );
  }
  if (!Number.isFinite(payload.exp) || payload.exp <= now - 5) {
    throw gatewayError(
      "Jeton appareil expiré",
      401,
      "device-access/token-expired"
    );
  }
  if (!Number.isFinite(payload.iat) || payload.iat > now + 5) {
    throw gatewayError(
      "Date d'émission du jeton appareil invalide",
      401,
      "device-access/token-invalid"
    );
  }
  if (!Number.isFinite(payload.auth_time) || payload.auth_time > now + 5) {
    throw gatewayError(
      "Date d'authentification appareil invalide",
      401,
      "device-access/token-invalid"
    );
  }

  return { ...payload, uid: payload.sub };
}

export async function readDeviceBinding({
  databaseURL,
  uid,
  token,
  fetchImpl = globalThis.fetch
} = {}) {
  const origin = String(databaseURL || "").replace(/\/+$/, "");
  if (!origin.startsWith("https://") || typeof fetchImpl !== "function") {
    throw gatewayError(
      "Validation deviceAccess non configurée",
      503,
      "device-access/unavailable"
    );
  }
  if (!uid || String(uid).length > 128 || !token) {
    throw gatewayError(
      "Identité appareil incomplète",
      401,
      "device-access/identity-missing"
    );
  }

  const url = new URL(`${origin}/deviceAccess/${encodeURIComponent(uid)}.json`);
  url.searchParams.set("auth", token);
  let response;
  try {
    response = await fetchImpl(url, {
      headers: { accept: "application/json", "user-agent": "EtR-Gateway/2.1" },
      signal: AbortSignal.timeout(10_000)
    });
  } catch (error) {
    throw gatewayError(
      "Realtime Database indisponible pour vérifier l'appareil",
      503,
      "device-access/network-error",
      error
    );
  }

  if (response.status === 401 || response.status === 403) {
    const code = response.status === 401
      ? "device-access/firebase-401"
      : "device-access/firebase-403";
    throw gatewayError(
      "Session appareil refusée par Firebase",
      401,
      code
    );
  }
  if (!response.ok) {
    throw gatewayError(
      "Realtime Database indisponible pour vérifier l'appareil",
      503,
      `device-access/firebase-${response.status}`
    );
  }

  let value;
  try {
    value = await response.json();
  } catch (error) {
    throw gatewayError(
      "Réponse deviceAccess invalide",
      503,
      "device-access/invalid-payload",
      error
    );
  }
  if (value === null) return null;
  if (typeof value !== "string" || !/^[A-Za-z0-9._-]{2,80}$/.test(value)) {
    throw gatewayError(
      "Liaison deviceAccess invalide",
      503,
      "device-access/invalid-binding"
    );
  }
  return value;
}

export function createDeviceConnectionAuthorizer({
  projectId,
  databaseURL,
  fetchImpl = globalThis.fetch
} = {}) {
  if (!projectId) {
    throw new Error("Device connection authorizer requires projectId");
  }

  return async function authorizeDeviceConnection({ token, installationId }) {
    // Structural Firebase claim validation is followed immediately by an RTDB
    // read authenticated with the same token. RTDB performs the cryptographic
    // token validation and its rules require auth.uid to equal this UID.
    const decoded = decodeFirebaseDeviceIdentity(token, { projectId });
    const linkedInstallationId = await readDeviceBinding({
      databaseURL,
      uid: decoded.uid,
      token,
      fetchImpl
    });
    return {
      decoded,
      linkedInstallationId,
      allowed: linkedInstallationId === installationId
    };
  };
}
