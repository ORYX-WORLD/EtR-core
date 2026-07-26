import assert from "node:assert/strict";
import test from "node:test";
import { createFirebasePasswordSessionIssuer } from "./device-session.mjs";

function fixture({ signInStatus = 200 } = {}) {
  const users = new Map();
  const claims = new Map();
  const calls = [];
  const auth = {
    async getUser(uid) {
      if (!users.has(uid)) throw Object.assign(new Error("not found"), { code: "auth/user-not-found" });
      return users.get(uid);
    },
    async createUser(data) {
      users.set(data.uid, structuredClone(data));
      calls.push({ kind: "create", data: structuredClone(data) });
      return data;
    },
    async updateUser(uid, data) {
      const next = { ...(users.get(uid) || { uid }), ...structuredClone(data) };
      users.set(uid, next);
      calls.push({ kind: "update", uid, data: structuredClone(data) });
      return next;
    },
    async setCustomUserClaims(uid, value) {
      claims.set(uid, structuredClone(value));
      calls.push({ kind: "claims", uid, value: structuredClone(value) });
    },
    async deleteUser(uid) {
      if (!users.has(uid)) throw Object.assign(new Error("not found"), { code: "auth/user-not-found" });
      users.delete(uid);
      claims.delete(uid);
      calls.push({ kind: "delete", uid });
    }
  };
  const fetchImpl = async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push({ kind: "signin", url: String(url), body: structuredClone(body) });
    if (signInStatus !== 200) {
      return { ok: false, status: signInStatus, text: async () => JSON.stringify({ error: { message: "INVALID_PASSWORD" } }) };
    }
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        idToken: "header.payload.signature",
        refreshToken: "r".repeat(64),
        expiresIn: "3600"
      })
    };
  };
  const issuer = createFirebasePasswordSessionIssuer({
    auth,
    apiKey: "test-public-firebase-api-key-123456789",
    fetchImpl,
    randomBytes: size => Buffer.alloc(size, 7)
  });
  return { issuer, auth, users, claims, calls };
}

test("creates a technical Firebase user and returns only tokens", async () => {
  const { issuer, users, claims, calls } = fixture();
  const result = await issuer.issue({
    deviceUid: "etrdev_0123456789abcdef0123456789abcdef0123456789abcdef",
    installationId: "etr-abcdef123456"
  });
  assert.equal(result.idToken, "header.payload.signature");
  assert.equal(result.refreshToken, "r".repeat(64));
  assert.equal(result.authenticationMethod, "server_generated_password_session");
  assert.equal(Object.hasOwn(result, "password"), false);
  assert.equal(Object.hasOwn(result, "email"), false);
  const user = [...users.values()][0];
  assert.match(user.email, /@devices\.oryx\.invalid$/);
  assert.equal(user.emailVerified, true);
  assert.equal(user.disabled, false);
  assert.equal(user.password.length > 60, true);
  assert.deepEqual(claims.get(user.uid), { etrDevice: true, installationId: "etr-abcdef123456" });
  const signIn = calls.find(call => call.kind === "signin");
  assert.equal(signIn.body.email, user.email);
  assert.equal(signIn.body.password, user.password);
  assert.equal(JSON.stringify(result).includes(user.password), false);
});

test("rotates the inaccessible technical password on re-enrollment", async () => {
  let counter = 1;
  const base = fixture();
  base.issuer = createFirebasePasswordSessionIssuer({
    auth: base.auth,
    apiKey: "test-public-firebase-api-key-123456789",
    fetchImpl: async (_url, options) => {
      base.calls.push({ kind: "signin", body: JSON.parse(options.body) });
      return { ok: true, status: 200, text: async () => JSON.stringify({ idToken: "a.b.c", refreshToken: "x".repeat(64), expiresIn: "3600" }) };
    },
    randomBytes: size => Buffer.alloc(size, counter++)
  });
  const uid = "etrdev_abcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdef";
  await base.issuer.issue({ deviceUid: uid, installationId: "etr-abcdefabcdef" });
  const firstPassword = base.users.get(uid).password;
  await base.issuer.issue({ deviceUid: uid, installationId: "etr-abcdefabcdef" });
  const secondPassword = base.users.get(uid).password;
  assert.notEqual(firstPassword, secondPassword);
  assert.equal(base.calls.some(call => call.kind === "update"), true);
});

test("probe creates a disposable user, verifies token issuance and deletes it", async () => {
  const { issuer, users, calls } = fixture();
  const result = await issuer.probe("workflow-run-123");
  assert.deepEqual(result, {
    ok: true,
    issuer: "firebase-password-session",
    credentialsReturned: false
  });
  assert.equal(users.size, 0);
  assert.equal(calls.some(call => call.kind === "delete"), true);
  assert.equal(JSON.stringify(result).includes("header.payload.signature"), false);
  assert.equal(JSON.stringify(result).includes("rrrr"), false);
});

test("Identity Toolkit failures are converted to a generic service error", async () => {
  const { issuer } = fixture({ signInStatus: 400 });
  await assert.rejects(
    issuer.issue({ deviceUid: "etrdev_1234567890abcdef1234567890abcdef1234567890abcdef", installationId: "etr-1234567890ab" }),
    error => error.status === 503 && error.code === "device_session_refused" && !String(error.message).includes("PASSWORD")
  );
});

test("configuration rejects a missing public Firebase API key", () => {
  const { auth } = fixture();
  assert.throws(
    () => createFirebasePasswordSessionIssuer({ auth, apiKey: "" }),
    /FIREBASE_WEB_API_KEY/
  );
});
