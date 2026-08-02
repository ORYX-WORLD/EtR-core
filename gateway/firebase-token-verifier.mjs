import { createRemoteJWKSet, decodeJwt, decodeProtectedHeader, jwtVerify } from "jose";

const FIREBASE_JWKS_URL = new URL(
  "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
);
const IDENTITY_TOOLKIT_ORIGIN = "https://identitytoolkit.googleapis.com";

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

function accountLookupErrorCode(body, status) {
  const raw = String(body?.error?.message || "").trim();
  if (raw === "USER_NOT_FOUND") return "auth/user-not-found";
  if (raw === "USER_DISABLED") return "auth/user-disabled";
  if (raw === "TOKEN_EXPIRED") return "auth/id-token-expired";
  if (raw === "INVALID_ID_TOKEN" || raw === "CREDENTIAL_TOO_OLD_LOGIN_AGAIN") {
    return "auth/invalid-id-token";
  }
  return `auth/account-lookup-${status}`;
}

function validSinceSeconds(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) return null;
  return parsed > 10_000_000_000 ? parsed / 1000 : parsed;
}

export async function lookupFirebaseAccount({
  token,
  apiKey,
  fetchImpl = globalThis.fetch
} = {}) {
  if (!apiKey || typeof fetchImpl !== "function") {
    throw unavailable(
      "Vérification du compte Firebase non configurée",
      "auth/account-lookup-unavailable"
    );
  }

  const url = new URL("/v1/accounts:lookup", IDENTITY_TOOLKIT_ORIGIN);
  url.searchParams.set("key", apiKey);
  let response;
  try {
    response = await fetchImpl(url, {
      method: "POST",
      headers: {
        accept: "application/json",
        "content-type": "application/json",
        "user-agent": "EtR-Gateway/2.0"
      },
      body: JSON.stringify({ idToken: token }),
      signal: AbortSignal.timeout(10_000)
    });
  } catch (error) {
    throw unavailable(
      "Identity Toolkit indisponible pour vérifier le compte",
      "auth/account-lookup-unavailable",
      error
    );
  }

  let body = {};
  try {
    body = await response.json();
  } catch (error) {
    if (response.ok) {
      throw unavailable(
        "Réponse Identity Toolkit invalide",
        "auth/account-lookup-invalid-payload",
        error
      );
    }
  }

  if (!response.ok) {
    const code = accountLookupErrorCode(body, response.status);
    if (response.status >= 500) {
      throw unavailable(
        "Identity Toolkit indisponible pour vérifier le compte",
        code
      );
    }
    throw unauthorized("Compte Firebase refusé", code);
  }

  const users = Array.isArray(body?.users) ? body.users : [];
  if (users.length !== 1 || !users[0] || typeof users[0] !== "object") {
    throw unauthorized("Compte Firebase introuvable", "auth/user-not-found");
  }
  return users[0];
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

async function enforceVerifiedHumanOrBoundDevice({
  token,
  payload,
  account,
  databaseURL,
  fetchImpl
}) {
  const privilegedOrDevice = payload?.oryxStaff === true ||
    payload?.oryxDeveloper === true ||
    payload?.etrDevice === true;
  if (privilegedOrDevice) return;

  if (payload?.email_verified === true && account?.emailVerified === true) return;

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

function enforceAccountState({ account, payload }) {
  const localId = String(account?.localId || "").trim();
  if (!localId || localId !== payload.sub) {
    throw unauthorized("Compte Firebase incohérent", "auth/user-mismatch");
  }
  if (account?.disabled === true) {
    throw unauthorized("Compte Firebase désactivé", "auth/user-disabled");
  }

  const validSince = validSinceSeconds(account?.validSince);
  if (validSince !== null && payload.auth_time < validSince) {
    throw unauthorized("Jeton Firebase révoqué", "auth/id-token-revoked");
  }
}

export function createFirebaseIdTokenVerifier({
  projectId,
  apiKey = process.env.FIREBASE_API_KEY,
  jwks,
  databaseURL,
  fetchImpl = globalThis.fetch,
  onFallback = () => {}
} = {}) {
  if (!projectId || !apiKey || typeof fetchImpl !== "function") {
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
    if (Date.now() < fallbackUntil) {
      payload = validateFallbackClaims(token, projectId);
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

    // Identity Toolkit validates the end-user ID token and returns the account
    // state without requiring the Cloud Run identity to hold Firebase Auth
    // administrator permissions. This replaces auth.getUser(), which caused
    // `auth/insufficient-permission` for otherwise valid verified clients.
    const account = await lookupFirebaseAccount({ token, apiKey, fetchImpl });
    enforceAccountState({ account, payload });
    await enforceVerifiedHumanOrBoundDevice({
      token,
      payload,
      account,
      databaseURL,
      fetchImpl
    });

    return { ...payload, uid: payload.sub };
  };
}
