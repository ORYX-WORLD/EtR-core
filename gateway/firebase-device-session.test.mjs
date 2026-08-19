import assert from "node:assert/strict";
import test from "node:test";
import { createFirebaseDeviceSessionIssuer, FIREBASE_DEVICE_SESSION_POLICY } from "./firebase-device-session.mjs";

function successfulFetch(calls) {
  return async (url, options) => {
    const entry = { url: String(url), options };
    calls.push(entry);
    if (entry.url.includes("accounts:delete")) {
      return { ok: true, status: 200, async json() { return {}; } };
    }
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          localId: "firebase-generated-device-uid",
          idToken: "header.payload.signature",
          refreshToken: "r".repeat(80),
          expiresIn: "3600"
        };
      }
    };
  };
}

function deterministicRandom(size) {
  return Buffer.alloc(size, 7);
}

test("issues a Firebase device session through public Identity Toolkit without Admin Auth", async () => {
  const requests = [];
  const issuer = createFirebaseDeviceSessionIssuer({
    apiKey: "public-firebase-api-key-1234567890",
    fetchImpl: successfulFetch(requests),
    randomBytes: deterministicRandom
  });
  const result = await issuer.issue("etrdev_abcdef0123456789", { installationId: "etr-abcdef012345" });
  assert.deepEqual(result, {
    uid: "firebase-generated-device-uid",
    idToken: "header.payload.signature",
    refreshToken: "r".repeat(80),
    expiresIn: 3600,
    authMode: "password_session"
  });
  assert.equal(issuer.managesUsers, false);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url.includes("accounts:signUp"), true);
  const body = JSON.parse(requests[0].options.body);
  assert.match(body.email, /^etrdev_[a-f0-9]{48}@devices\.oryx\.invalid$/);
  assert.equal(body.password.length >= 60, true);
  assert.equal(body.returnSecureToken, true);
  assert.equal(JSON.stringify(result).includes(body.password), false);
});

test("health creates and deletes a disposable Firebase account without returning tokens", async () => {
  const requests = [];
  const issuer = createFirebaseDeviceSessionIssuer({
    apiKey: "public-firebase-api-key-1234567890",
    fetchImpl: successfulFetch(requests),
    randomBytes: deterministicRandom
  });
  const result = await issuer.health("123456789");
  assert.deepEqual(result, { ok: true, mode: "firebase-password-session", tokenExchange: true });
  assert.equal(requests.length, 2);
  assert.equal(requests[0].url.includes("accounts:signUp"), true);
  assert.equal(requests[1].url.includes("accounts:delete"), true);
  assert.deepEqual(JSON.parse(requests[1].options.body), { idToken: "header.payload.signature" });
  assert.equal(JSON.stringify(result).includes("refreshToken"), false);
});

test("rejects missing API key, invalid UID hint and invalid installation ID", async () => {
  assert.throws(
    () => createFirebaseDeviceSessionIssuer({ apiKey: "short", fetchImpl: successfulFetch([]) }),
    /FIREBASE_API_KEY/
  );
  const issuer = createFirebaseDeviceSessionIssuer({
    apiKey: "public-firebase-api-key-1234567890",
    fetchImpl: successfulFetch([])
  });
  await assert.rejects(issuer.issue("owner-user", { installationId: "etr-test" }), /invalid_device_uid/);
  await assert.rejects(issuer.issue("etrdev_abcdef0123456789", { installationId: "bad" }), /invalid_installation_id/);
});

test("maps Identity Toolkit failures to a non-sensitive session error", async () => {
  const issuer = createFirebaseDeviceSessionIssuer({
    apiKey: "public-firebase-api-key-1234567890",
    fetchImpl: async () => ({
      ok: false,
      status: 400,
      async json() { return { error: { message: "SENSITIVE_PROVIDER_DETAIL" } }; }
    })
  });
  await assert.rejects(
    issuer.issue("etrdev_abcdef0123456789", { installationId: "etr-abcdef012345" }),
    error => error.code === "auth/device-session-unavailable" &&
      error.status === 502 &&
      !String(error.message).includes("SENSITIVE")
  );
});

test("declares the public, non-Admin session policy", () => {
  assert.deepEqual(FIREBASE_DEVICE_SESSION_POLICY, {
    mode: "password_session",
    passwordEntropyBits: 384,
    internalDomain: "devices.oryx.invalid",
    adminAuthRequired: false,
    customClaimsRequired: false,
    firebaseUidSource: "identity-toolkit-sign-up"
  });
});
