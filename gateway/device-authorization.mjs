function gatewayError(message, status, code, cause) {
  return Object.assign(new Error(message, { cause }), { status, code });
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
      headers: { accept: "application/json", "user-agent": "EtR-Gateway/2.0" },
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
  verifyIdToken,
  databaseURL,
  fetchImpl = globalThis.fetch
} = {}) {
  if (typeof verifyIdToken !== "function") {
    throw new Error("Device connection authorizer requires verifyIdToken");
  }

  return async function authorizeDeviceConnection({ token, installationId }) {
    const decoded = await verifyIdToken(token);
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
