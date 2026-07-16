import assert from "node:assert/strict";
import test from "node:test";
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT } from "jose";
import { createFirebaseIdTokenVerifier } from "./firebase-token-verifier.mjs";

const projectId = "oryx-froid-industriel";
const now = Math.floor(Date.now() / 1000);
const { privateKey, publicKey } = await generateKeyPair("RS256");
const publicJwk = await exportJWK(publicKey);
publicJwk.kid = "test-key";
publicJwk.alg = "RS256";
publicJwk.use = "sig";
const jwks = createLocalJWKSet({ keys: [publicJwk] });

async function token(overrides = {}) {
  const claims = {
    auth_time: now,
    ...overrides
  };
  return new SignJWT(claims)
    .setProtectedHeader({ alg: "RS256", kid: "test-key" })
    .setSubject("device-uid")
    .setAudience(projectId)
    .setIssuer(`https://securetoken.google.com/${projectId}`)
    .setIssuedAt(now)
    .setExpirationTime(now + 3600)
    .sign(privateKey);
}

test("accepts a valid, non-revoked Firebase ID token", async () => {
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    jwks,
    auth: {
      getUser: async () => ({
        disabled: false,
        tokensValidAfterTime: new Date((now - 60) * 1000).toISOString()
      })
    }
  });

  const decoded = await verify(await token());
  assert.equal(decoded.uid, "device-uid");
});

test("rejects a token issued before the revocation timestamp", async () => {
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    jwks,
    auth: {
      getUser: async () => ({
        disabled: false,
        tokensValidAfterTime: new Date((now + 1) * 1000).toISOString()
      })
    }
  });

  await assert.rejects(verify(await token()), (error) => {
    assert.equal(error.status, 401);
    assert.equal(error.code, "auth/id-token-revoked");
    return true;
  });
});

test("rejects a token for another Firebase project", async () => {
  const foreignToken = new SignJWT({ auth_time: now })
    .setProtectedHeader({ alg: "RS256", kid: "test-key" })
    .setSubject("device-uid")
    .setAudience("another-project")
    .setIssuer("https://securetoken.google.com/another-project")
    .setIssuedAt(now)
    .setExpirationTime(now + 3600)
    .sign(privateKey);
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    jwks,
    auth: { getUser: async () => ({ disabled: false }) }
  });

  await assert.rejects(verify(foreignToken), (error) => {
    assert.equal(error.status, 401);
    return true;
  });
});


test("falls back to Realtime Database when Google refuses the JWKS response", async () => {
  const requests = [];
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    databaseURL: "https://example-default-rtdb.europe-west1.firebasedatabase.app",
    jwks: async () => {
      throw Object.assign(
        new Error("Expected 200 OK from the JSON Web Key Set HTTP response"),
        { code: "ERR_JOSE_GENERIC" }
      );
    },
    fetchImpl: async (url) => {
      requests.push(String(url));
      return { ok: true, status: 200 };
    },
    auth: {
      getUser: async () => {
        throw new Error("getUser must not be called in fallback mode");
      }
    }
  });

  const decoded = await verify(await token());
  assert.equal(decoded.uid, "device-uid");
  assert.equal(requests.length, 1);
  const requestUrl = new URL(requests[0]);
  assert.equal(requestUrl.pathname, "/deviceAccess/device-uid.json");
  assert.equal(requestUrl.searchParams.get("auth")?.split(".").length, 3);
});

test("rejects a token refused by Realtime Database fallback", async () => {
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    databaseURL: "https://example-default-rtdb.europe-west1.firebasedatabase.app",
    jwks: async () => {
      throw Object.assign(
        new Error("Expected 200 OK from the JSON Web Key Set HTTP response"),
        { code: "ERR_JOSE_GENERIC" }
      );
    },
    fetchImpl: async () => ({ ok: false, status: 401 }),
    auth: { getUser: async () => ({ disabled: false }) }
  });

  await assert.rejects(verify(await token()), (error) => {
    assert.equal(error.status, 401);
    assert.equal(error.code, "auth/realtime-database-401");
    return true;
  });
});
