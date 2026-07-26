import assert from "node:assert/strict";
import test from "node:test";
import { createFirebaseDeviceSessionIssuer, FIREBASE_DEVICE_SESSION_POLICY } from "./firebase-device-session.mjs";

function authFixture({ existing = true } = {}) {
  const calls = [];
  return {
    calls,
    async getUser(uid) {
      calls.push(["getUser", uid]);
      if (!existing) throw Object.assign(new Error("not found"), { code: "auth/user-not-found" });
      return { uid };
    },
    async createUser(profile) { calls.push(["createUser", profile]); return profile; },
    async updateUser(uid, profile) { calls.push(["updateUser", uid, profile]); return { uid, ...profile }; },
    async setCustomUserClaims(uid, claims) { calls.push(["setCustomUserClaims", uid, claims]); },
    async revokeRefreshTokens(uid) { calls.push(["revokeRefreshTokens", uid]); },
    async deleteUser(uid) { calls.push(["deleteUser", uid]); }
  };
}

function successfulFetch(calls) {
  return async (url, options) => {
    calls.push({ url: String(url), options });
    return {
      ok: true,
      status: 200,
      async json() {
        return {
          idToken: "header.payload.signature",
          refreshToken: "r".repeat(80),
          expiresIn: "3600"
        };
      }
    };
  };
}

test("issues a Firebase device session without custom-token signing", async () => {
  const auth = authFixture();
  const requests = [];
  const issuer = createFirebaseDeviceSessionIssuer({
    auth,
    apiKey: "public-firebase-api-key-1234567890",
    fetchImpl: successfulFetch(requests),
    randomBytes: size => Buffer.alloc(size, 7)
  });
  const result = await issuer.issue("etrdev_abcdef0123456789", { installationId: "etr-abcdef012345" });
  assert.deepEqual(result, {
    idToken: "header.payload.signature",
    refreshToken: "r".repeat(80),
    expiresIn: 3600,
    authMode: "password_session"
  });
  assert.equal(JSON.stringify(result).includes("BwcHBw"), false);
  const update = auth.calls.find(call => call[0] === "updateUser");
  assert.equal(update[2].email, "etrdev_abcdef0123456789@devices.oryx.invalid");
  assert.equal(update[2].emailVerified, true);
  assert.equal(update[2].password.length >= 60, true);
  assert.deepEqual(auth.calls.find(call => call[0] === "setCustomUserClaims").slice(1), [
    "etrdev_abcdef0123456789",
    { etrDevice: true, installationId: "etr-abcdef012345" }
  ]);
  const body = JSON.parse(requests[0].options.body);
  assert.equal(body.email, update[2].email);
  assert.equal(body.password, update[2].password);
  assert.equal(requests[0].url.includes("signInWithPassword"), true);
});

test("creates the deterministic device user when absent", async () => {
  const auth = authFixture({ existing: false });
  const issuer = createFirebaseDeviceSessionIssuer({
    auth,
    apiKey: "public-firebase-api-key-1234567890",
    fetchImpl: successfulFetch([]),
    randomBytes: size => Buffer.alloc(size, 11)
  });
  await issuer.issue("etrdev_0123456789abcdef", { installationId: "etr-0123456789ab" });
  assert.equal(auth.calls.some(call => call[0] === "createUser"), true);
  assert.equal(auth.calls.some(call => call[0] === "updateUser"), false);
});

test("health creates and deletes a disposable identity without returning tokens", async () => {
  const auth = authFixture({ existing: false });
  const issuer = createFirebaseDeviceSessionIssuer({
    auth,
    apiKey: "public-firebase-api-key-1234567890",
    fetchImpl: successfulFetch([]),
    randomBytes: size => Buffer.alloc(size, 13)
  });
  const result = await issuer.health("123456789");
  assert.deepEqual(result, { ok: true, mode: "firebase-password-session", tokenExchange: true });
  assert.equal(JSON.stringify(result).includes("refreshToken"), false);
  assert.equal(auth.calls.some(call => call[0] === "deleteUser"), true);
});

test("rejects missing API key and invalid device UID", () => {
  const auth = authFixture();
  assert.throws(() => createFirebaseDeviceSessionIssuer({ auth, apiKey: "short", fetchImpl: successfulFetch([]) }), /FIREBASE_API_KEY/);
  const issuer = createFirebaseDeviceSessionIssuer({ auth, apiKey: "public-firebase-api-key-1234567890", fetchImpl: successfulFetch([]) });
  assert.rejects(issuer.issue("owner-user", { installationId: "etr-test" }), /invalid_device_uid/);
});

test("maps Firebase REST failure to a non-sensitive device-session error", async () => {
  const auth = authFixture();
  const issuer = createFirebaseDeviceSessionIssuer({
    auth,
    apiKey: "public-firebase-api-key-1234567890",
    fetchImpl: async () => ({ ok: false, status: 400, async json() { return { error: { message: "SENSITIVE_PROVIDER_DETAIL" } }; } })
  });
  await assert.rejects(
    issuer.issue("etrdev_abcdef0123456789", { installationId: "etr-abcdef012345" }),
    error => error.code === "auth/device-session-unavailable" && !String(error.message).includes("SENSITIVE")
  );
});

test("declares a 384-bit ephemeral password policy", () => {
  assert.deepEqual(FIREBASE_DEVICE_SESSION_POLICY, {
    mode: "password_session",
    passwordEntropyBits: 384,
    internalDomain: "devices.oryx.invalid"
  });
});
