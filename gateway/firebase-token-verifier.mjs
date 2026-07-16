import { createRemoteJWKSet, jwtVerify } from "jose";

const FIREBASE_JWKS_URL = new URL(
  "https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com"
);

function unauthorized(message, code, cause) {
  return Object.assign(new Error(message, { cause }), { status: 401, code });
}

export function createFirebaseIdTokenVerifier({ projectId, auth, jwks } = {}) {
  if (!projectId || !auth?.getUser) {
    throw new Error("Firebase token verifier is not configured");
  }

  const keySet = jwks || createRemoteJWKSet(FIREBASE_JWKS_URL, {
    cooldownDuration: 30_000,
    timeoutDuration: 10_000
  });
  const issuer = `https://securetoken.google.com/${projectId}`;

  return async function verifyFirebaseIdToken(token) {
    if (!token) throw unauthorized("Jeton manquant", "auth/id-token-missing");

    let payload;
    try {
      ({ payload } = await jwtVerify(token, keySet, {
        algorithms: ["RS256"],
        audience: projectId,
        issuer,
        requiredClaims: ["sub", "iat", "exp", "auth_time"],
        clockTolerance: 5
      }));
    } catch (error) {
      throw unauthorized("Jeton Firebase invalide", error?.code || "auth/invalid-id-token", error);
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

    return { ...payload, uid: payload.sub };
  };
}
