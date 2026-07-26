import { createRemoteJWKSet, decodeJwt, decodeProtectedHeader, jwtVerify } from "jose";

const FIREBASE_JWKS_URL = new URL(
  "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
);

function unauthorized(message, code, cause) {
  return Object.assign(new Error(message, { cause }), { status: 401, code });
}

function unavailable(message, code, cause) {
  return Object.assign(new Error(message, { cause }), { status: 503, code });
}

export function principalHasVerifiedAccess(payload) {
  return payload?.oryxStaff === true ||
    payload?.oryxDeveloper === true ||
    payload?.etrDevice === true ||
    payload?.email_verified === true;
}

function validateFallbackClaims(token, projectId) {
  let header;
  let payload;
  try {
    header = decodeProtectedHeader(token);
    payload = decodeJwt(token);
  } catch (error) {
    throw unauthorized("Jeton Firebase invalide", error?.code || "auth/invalid-id-token", error);
  }

  const now = Math.floor(Date.now() / 1000);
  const issuer = `https://securetoken.google.com/${projectId}`;
  if (header.alg !== "RS256") {
    throw unauthorized("Algorithme Firebase invalide", "auth/invalid-id-token");
  }
  if (payload.aud !== projectId || payload.iss !== issuer) {
    throw unauthorized("Projet Firebase invalide", "auth/invalid-id-token");
  }
  if (typeof payload.sub !== "string" || payload.sub.length === 0 || payload.sub.length > 128) {
    throw unauthorized("UID Firebase invalide", "auth/invalid-id-token");
  }
  if (!Number.isFinite(payload.exp) || payload.exp <= now - 5) {
    throw unauthorized("Jeton Firebase expiré", "auth/id-token-expired");
  }
  if (!Number.isFinite(payload.iat) || payload.iat > now + 5) {
    throw unauthorized("Date d'émission Firebase invalide", "auth/invalid-id-token");
  }
  if (!Number.isFinite(payload.auth_time) || payload.auth_time > now + 5) {
    throw unauthorized("Date d'authentification Firebase invalide", "auth/invalid-id-token");
  }
  return payload;
}

function isJwksTransportFailure(error) {
  return error?.code === "ERR_JOSE_GENERIC" &&
    String(error?.message || "").includes("Expected 200 OK");
}

async function verifyThroughRealtimeDatabase({
  token,
  payload,
  databaseURL,
  fetchImpl
}) {
  if (!databaseURL || typeof fetchImpl !== "function") {
    throw unavailable(
      "Validation Firebase de secours non configurée",
      "auth/firebase-validation-unavailable"
    );
  }

  const url = new URL(
    `${databaseURL.replace(/\/+$/, "")}/deviceAccess/${encodeURIComponent(payload.sub)}.json`
  );
  url.searchParams.set("auth", token);

  let response;
  try {
    response = await fetchImpl(url, {
      headers: { accept: "application/json" },
      signal: AbortSignal.timeout(10_000)
    });
  } catch (error) {
    throw unavailable(
      "Realtime Database indisponible pour valider le jeton",
      "auth/firebase-validation-unavailable",
      error
    );
  }

  if (!response.ok) {
    throw unauthorized(
      "Jeton Firebase refusé par Realtime Database",
      `auth/realtime-database-${response.status}`
    );
  }
}

async function enforceVerifiedHumanOrBoundDevice({ token, payload, databaseURL, fetchImpl }) {
  if (principalHasVerifiedAccess(payload)) return;
  try {
    // Compatibility path for a legacy technical account already bound in
    // deviceAccess but created before the etrDevice custom claim existed.
    await verifyThroughRealtimeDatabase({ token, payload, databaseURL, fetchImpl });
  } catch (error) {
    if (error?.status === 401) {
      throw unauthorized(
        "Adresse e-mail Firebase non vérifiée",
        "auth/email-not-verified",
        error
      );
    }
    throw error;
  }
}

export function createFirebaseIdTokenVerifier({
  projectId,
  auth,
  jwks,
  databaseURL,
  fetchImpl = globalThis.fetch,
  onFallback = () => {}
} = {}) {
  if (!projectId || !auth?.getUser) {
    throw new Error("Firebase token verifier is not configured");
  }

  const keySet = jwks || createRemoteJWKSet(FIREBASE_JWKS_URL, {
    cooldownDuration: 30_000,
    timeoutDuration: 10_000
  });
  const issuer = `https://securetoken.google.com/${projectId}`;
  let fallbackUntil = 0;

  return async function verifyFirebaseIdToken(token) {
    if (!token) throw unauthorized("Jeton manquant", "auth/id-token-missing");

    let payload;
    let usedFallback = false;
    if (Date.now() < fallbackUntil) {
      payload = validateFallbackClaims(token, projectId);
      await verifyThroughRealtimeDatabase({ token, payload, databaseURL, fetchImpl });
      usedFallback = true;
    } else {
      try {
        ({ payload } = await jwtVerify(token, keySet, {
          algorithms: ["RS256"],
          audience: projectId,
          issuer,
          requiredClaims: ["sub", "iat", "exp", "auth_time"],
          clockTolerance: 5
        }));
      } catch (error) {
        if (!isJwksTransportFailure(error)) {
          throw unauthorized(
            "Jeton Firebase invalide",
            error?.code || "auth/invalid-id-token",
            error
          );
        }
        fallbackUntil = Date.now() + 5 * 60_000;
        onFallback(error);
        payload = validateFallbackClaims(token, projectId);
        await verifyThroughRealtimeDatabase({ token, payload, databaseURL, fetchImpl });
        usedFallback = true;
      }
    }

    const now = Math.floor(Date.now() / 1000);
    if (typeof payload.sub !== "string" || payload.sub.length === 0 || payload.sub.length > 128) {
      throw unauthorized("UID Firebase invalide", "auth/invalid-id-token");
    }
    if (!Number.isFinite(payload.iat) || payload.iat > now + 5) {
      throw unauthorized("Date d'émission Firebase invalide", "auth/invalid-id-token");
    }
    if (!Number.isFinite(payload.auth_time) || payload.auth_time > now + 5) {
      throw unauthorized("Date d'authentification Firebase invalide", "auth/invalid-id-token");
    }

    if (!usedFallback) {
      let user;
      try {
        user = await auth.getUser(payload.sub);
      } catch (error) {
        if (error?.code === "auth/user-not-found") {
          throw unauthorized("Compte Firebase introuvable", error.code, error);
        }
        throw error;
      }
      if (user.disabled) {
        throw unauthorized("Compte Firebase désactivé", "auth/user-disabled");
      }

      const tokensValidAfter = Date.parse(user.tokensValidAfterTime || "") / 1000;
      if (Number.isFinite(tokensValidAfter) && payload.auth_time < tokensValidAfter) {
        throw unauthorized("Jeton Firebase révoqué", "auth/id-token-revoked");
      }

      await enforceVerifiedHumanOrBoundDevice({ token, payload, databaseURL, fetchImpl });
    }

    return { ...payload, uid: payload.sub };
  };
}
