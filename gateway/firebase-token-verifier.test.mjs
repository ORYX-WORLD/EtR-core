import assert from "node:assert/strict";
import test from "node:test";
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT } from "jose";
import {
  createFirebaseIdTokenVerifier,
  lookupFirebaseAccount,
  principalHasVerifiedAccess
} from "./firebase-token-verifier.mjs";

const projectId = "oryx-froid-industriel";
const apiKey = "public-firebase-api-key-for-tests";
const databaseURL = "https://example-default-rtdb.europe-west1.firebasedatabase.app";
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
    email: "client@example.com",
    email_verified: true,
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

function activeAccount(overrides = {}) {
  return {
    localId: "device-uid",
    email: "client@example.com",
    emailVerified: true,
    disabled: false,
    validSince: String(now - 60),
    ...overrides
  };
}

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return body; }
  };
}

function fetchRouter({ account = activeAccount(), rtdbStatus = 401, calls = [] } = {}) {
  return async (url, options = {}) => {
    const parsed = new URL(String(url));
    calls.push({ url: parsed, options });
    if (parsed.hostname === "identitytoolkit.googleapis.com") {
      return jsonResponse(200, { users: [account] });
    }
    if (parsed.hostname.endsWith("firebasedatabase.app")) {
      return jsonResponse(rtdbStatus, rtdbStatus === 200 ? "etr-core" : { error: "Permission denied" });
    }
    throw new Error(`Unexpected URL: ${parsed}`);
  };
}

function verifier(overrides = {}) {
  return createFirebaseIdTokenVerifier({
    projectId,
    apiKey,
    jwks,
    databaseURL,
    fetchImpl: fetchRouter(),
    ...overrides
  });
}

test("classifies verified humans, ORYX staff and technical devices", () => {
  assert.equal(principalHasVerifiedAccess({ email_verified: true }), true);
  assert.equal(principalHasVerifiedAccess({ oryxStaff: true }), true);
  assert.equal(principalHasVerifiedAccess({ oryxDeveloper: true }), true);
  assert.equal(principalHasVerifiedAccess({ etrDevice: true }), true);
  assert.equal(principalHasVerifiedAccess({ email_verified: false }), false);
  assert.equal(principalHasVerifiedAccess({}), false);
});

test("looks up the Firebase account with the end-user ID token", async () => {
  const calls = [];
  const idToken = await token();
  const account = await lookupFirebaseAccount({
    token: idToken,
    apiKey,
    fetchImpl: fetchRouter({ calls })
  });

  assert.equal(account.localId, "device-uid");
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url.pathname, "/v1/accounts:lookup");
  assert.equal(calls[0].url.searchParams.get("key"), apiKey);
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), { idToken });
});

test("accepts a valid verified human without calling Firebase Admin getUser", async () => {
  const calls = [];
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    apiKey,
    jwks,
    databaseURL,
    fetchImpl: fetchRouter({ calls })
  });

  const decoded = await verify(await token());
  assert.equal(decoded.uid, "device-uid");
  assert.equal(decoded.email_verified, true);
  assert.equal(calls.filter(call => call.url.hostname === "identitytoolkit.googleapis.com").length, 1);
  assert.equal(calls.filter(call => call.url.hostname.endsWith("firebasedatabase.app")).length, 0);
});

test("rejects an unverified human even when the Firebase account exists", async () => {
  const calls = [];
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    apiKey,
    jwks,
    databaseURL,
    fetchImpl: fetchRouter({
      account: activeAccount({ emailVerified: false }),
      rtdbStatus: 401,
      calls
    })
  });

  await assert.rejects(verify(await token({ email_verified: false })), (error) => {
    assert.equal(error.status, 401);
    assert.equal(error.code, "auth/email-not-verified");
    return true;
  });
  assert.equal(calls.filter(call => call.url.hostname.endsWith("firebasedatabase.app")).length, 1);
});

test("does not trust a verified claim when the account is not verified", async () => {
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    apiKey,
    jwks,
    databaseURL,
    fetchImpl: fetchRouter({
      account: activeAccount({ emailVerified: false }),
      rtdbStatus: 401
    })
  });

  await assert.rejects(verify(await token({ email_verified: true })), (error) => {
    assert.equal(error.code, "auth/email-not-verified");
    return true;
  });
});

test("accepts an EtR custom-token identity without an email claim", async () => {
  const calls = [];
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    apiKey,
    jwks,
    databaseURL,
    fetchImpl: fetchRouter({
      account: activeAccount({ email: "device@devices.oryx.invalid", emailVerified: false }),
      calls
    })
  });

  const decoded = await verify(await token({ email: undefined, email_verified: undefined, etrDevice: true }));
  assert.equal(decoded.etrDevice, true);
  assert.equal(calls.filter(call => call.url.hostname.endsWith("firebasedatabase.app")).length, 0);
});

test("accepts a legacy technical account only when deviceAccess binds it", async () => {
  const calls = [];
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    apiKey,
    jwks,
    databaseURL,
    fetchImpl: fetchRouter({
      account: activeAccount({ emailVerified: false }),
      rtdbStatus: 200,
      calls
    })
  });

  const decoded = await verify(await token({ email_verified: false, email: "technical@example.com" }));
  assert.equal(decoded.uid, "device-uid");
  const rtdbCall = calls.find(call => call.url.hostname.endsWith("firebasedatabase.app"));
  assert.equal(rtdbCall.url.pathname, "/deviceAccess/device-uid.json");
  assert.equal(rtdbCall.url.searchParams.get("auth")?.split(".").length, 3);
});

test("accepts an ORYX privileged token without requiring email verification", async () => {
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    apiKey,
    jwks,
    databaseURL,
    fetchImpl: fetchRouter({ account: activeAccount({ emailVerified: false }) })
  });
  const decoded = await verify(await token({ email_verified: false, oryxDeveloper: true }));
  assert.equal(decoded.oryxDeveloper, true);
});

test("rejects a disabled Firebase account", async () => {
  const verify = verifier({
    fetchImpl: fetchRouter({ account: activeAccount({ disabled: true }) })
  });
  await assert.rejects(verify(await token()), (error) => {
    assert.equal(error.status, 401);
    assert.equal(error.code, "auth/user-disabled");
    return true;
  });
});

test("rejects an account lookup whose localId does not match the JWT subject", async () => {
  const verify = verifier({
    fetchImpl: fetchRouter({ account: activeAccount({ localId: "another-user" }) })
  });
  await assert.rejects(verify(await token()), (error) => {
    assert.equal(error.status, 401);
    assert.equal(error.code, "auth/user-mismatch");
    return true;
  });
});

test("rejects a token issued before the account validSince timestamp", async () => {
  const verify = verifier({
    fetchImpl: fetchRouter({ account: activeAccount({ validSince: String(now + 1) }) })
  });
  await assert.rejects(verify(await token()), (error) => {
    assert.equal(error.status, 401);
    assert.equal(error.code, "auth/id-token-revoked");
    return true;
  });
});

test("rejects a token for another Firebase project before account lookup", async () => {
  const calls = [];
  const foreignToken = new SignJWT({ auth_time: now, email_verified: true })
    .setProtectedHeader({ alg: "RS256", kid: "test-key" })
    .setSubject("device-uid")
    .setAudience("another-project")
    .setIssuer("https://securetoken.google.com/another-project")
    .setIssuedAt(now)
    .setExpirationTime(now + 3600)
    .sign(privateKey);
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    apiKey,
    jwks,
    databaseURL,
    fetchImpl: fetchRouter({ calls })
  });

  await assert.rejects(verify(foreignToken), error => error.status === 401);
  assert.equal(calls.length, 0);
});

test("falls back to Identity Toolkit when Google refuses the JWKS response", async () => {
  const calls = [];
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    apiKey,
    databaseURL,
    jwks: async () => {
      throw Object.assign(
        new Error("Expected 200 OK from the JSON Web Key Set HTTP response"),
        { code: "ERR_JOSE_GENERIC" }
      );
    },
    fetchImpl: fetchRouter({
      account: activeAccount({ email: "device@devices.oryx.invalid", emailVerified: false }),
      calls
    })
  });

  const decoded = await verify(await token({ email_verified: false, etrDevice: true }));
  assert.equal(decoded.uid, "device-uid");
  assert.equal(calls.filter(call => call.url.hostname === "identitytoolkit.googleapis.com").length, 1);
  assert.equal(calls.filter(call => call.url.hostname.endsWith("firebasedatabase.app")).length, 0);
});

test("maps an invalid ID token from Identity Toolkit to an authentication error", async () => {
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    apiKey,
    jwks,
    databaseURL,
    fetchImpl: async () => jsonResponse(400, { error: { message: "INVALID_ID_TOKEN" } })
  });
  await assert.rejects(verify(await token()), (error) => {
    assert.equal(error.status, 401);
    assert.equal(error.code, "auth/invalid-id-token");
    return true;
  });
});

test("maps Identity Toolkit transport failures to a temporary service error", async () => {
  const verify = createFirebaseIdTokenVerifier({
    projectId,
    apiKey,
    jwks,
    databaseURL,
    fetchImpl: async () => { throw new Error("network unavailable"); }
  });
  await assert.rejects(verify(await token()), (error) => {
    assert.equal(error.status, 503);
    assert.equal(error.code, "auth/account-lookup-unavailable");
    return true;
  });
});
