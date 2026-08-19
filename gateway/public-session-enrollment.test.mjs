import assert from "node:assert/strict";
import test from "node:test";
import { createEnrollmentService, serialFingerprint } from "./enrollment.mjs";

function deterministicBytes() {
  let sequence = 1;
  return size => Buffer.alloc(size, sequence++ % 32);
}

function memoryStore({ failCompletion = false } = {}) {
  const state = {
    requests: new Map(),
    owners: new Map(),
    bindings: new Map(),
    unbound: [],
    claims: []
  };
  return {
    state,
    async getRequest(serialHash) { return state.requests.get(serialHash) || null; },
    async putRequest(serialHash, request) { state.requests.set(serialHash, structuredClone(request)); },
    async incrementAttempts(serialHash, timestamp) {
      const current = state.requests.get(serialHash);
      current.attempts = Number(current.attempts || 0) + 1;
      current.lastAttemptAt = timestamp;
    },
    async getOwner(installationId) { return state.owners.get(installationId) || null; },
    async claimRequest(serialHash, codeHash, ownerUid, ownerEmail, timestamp) {
      const current = state.requests.get(serialHash);
      if (!current || current.codeHash !== codeHash || current.status !== "pending") return null;
      Object.assign(current, { status: "claimed", ownerUid, ownerEmail, claimedAt: timestamp });
      return structuredClone(current);
    },
    async applyClaim(installationId, ownerUid, ownerEmail, timestamp) {
      state.owners.set(installationId, ownerUid);
      state.claims.push({ installationId, ownerUid, ownerEmail, timestamp });
    },
    async lockExchange(serialHash, codeHash, lockId, timestamp) {
      const current = state.requests.get(serialHash);
      if (!current || current.codeHash !== codeHash || current.status !== "claimed") return null;
      Object.assign(current, { status: "exchanging", exchangeLock: lockId, exchangeStartedAt: timestamp });
      return structuredClone(current);
    },
    async bindDevice(installationId, deviceUid, serialHash, timestamp) {
      state.bindings.set(deviceUid, { installationId, serialHash, timestamp });
    },
    async unbindDevice(installationId, deviceUid) {
      state.bindings.delete(deviceUid);
      state.unbound.push({ installationId, deviceUid });
    },
    async completeExchange(serialHash, lockId, deviceUid, timestamp) {
      if (failCompletion) return false;
      const current = state.requests.get(serialHash);
      if (!current || current.status !== "exchanging" || current.exchangeLock !== lockId) return false;
      Object.assign(current, {
        status: "exchanged",
        deviceUid,
        completedAt: timestamp,
        codeHash: null,
        rotationTokenHash: null,
        exchangeLock: null
      });
      return true;
    },
    async rollbackExchange(serialHash, lockId, timestamp) {
      const current = state.requests.get(serialHash);
      if (current?.status === "exchanging" && current.exchangeLock === lockId) {
        Object.assign(current, { status: "claimed", exchangeLock: null, lastExchangeFailureAt: timestamp });
      }
    }
  };
}

function publicSessionAuth() {
  const calls = [];
  return {
    calls,
    managesUsers: false,
    async getUser() { throw new Error("Admin Auth must not be called"); },
    async createUser() { throw new Error("Admin Auth must not be called"); },
    async createCustomToken(uidHint, claims) {
      calls.push(["issue", uidHint, claims]);
      return {
        uid: "firebase-public-generated-uid",
        idToken: "header.payload.signature",
        refreshToken: "r".repeat(80),
        expiresIn: 3600,
        authMode: "password_session"
      };
    },
    async revokeSession(session) { calls.push(["revoke", session.uid]); }
  };
}

async function claimedFixture({ failCompletion = false } = {}) {
  const store = memoryStore({ failCompletion });
  const auth = publicSessionAuth();
  const service = createEnrollmentService({
    store,
    auth,
    now: () => Date.parse("2026-08-18T12:00:00Z"),
    randomBytes: deterministicBytes()
  });
  const serial = "0000ABCD1234EF56";
  const request = await service.request({ serial, hostname: "etr-public-session" });
  await service.claim({
    serial,
    activationCode: request.activationCode,
    decodedUser: { uid: "owner-1", email: "owner@example.com", email_verified: true }
  });
  return { store, auth, service, serial, request };
}

test("binds the Firebase-generated UID and never calls Admin user management", async () => {
  const { store, auth, service, serial, request } = await claimedFixture();
  const exchange = await service.exchange({ serial, activationCode: request.activationCode });
  assert.equal(exchange.status, "exchanged");
  assert.equal(exchange.deviceUid, "firebase-public-generated-uid");
  assert.equal(exchange.customToken.uid, "firebase-public-generated-uid");
  assert.equal(store.state.bindings.get(exchange.deviceUid).installationId, request.installationId);
  assert.equal(store.state.requests.get(serialFingerprint(serial)).status, "exchanged");
  assert.equal(auth.calls.filter(call => call[0] === "issue").length, 1);
  assert.equal(auth.calls.filter(call => call[0] === "revoke").length, 0);
});

test("removes the binding, deletes the disposable account and restores the claim on completion failure", async () => {
  const { store, auth, service, serial, request } = await claimedFixture({ failCompletion: true });
  await assert.rejects(
    service.exchange({ serial, activationCode: request.activationCode }),
    /enrollment_completion_conflict/
  );
  assert.equal(store.state.bindings.size, 0);
  assert.deepEqual(store.state.unbound, [{
    installationId: request.installationId,
    deviceUid: "firebase-public-generated-uid"
  }]);
  assert.equal(auth.calls.some(call => call[0] === "revoke"), true);
  assert.equal(store.state.requests.get(serialFingerprint(serial)).status, "claimed");
});
