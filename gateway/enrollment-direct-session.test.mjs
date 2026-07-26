import assert from "node:assert/strict";
import test from "node:test";
import { createEnrollmentService, serialFingerprint } from "./enrollment.mjs";

function storeFixture() {
  const requests = new Map();
  const owners = new Map();
  const bindings = new Map();
  return {
    requests,
    owners,
    bindings,
    async getRequest(hash) { return requests.get(hash) || null; },
    async putRequest(hash, request) { requests.set(hash, structuredClone(request)); },
    async incrementAttempts(hash) { const item = requests.get(hash); item.attempts += 1; },
    async getOwner(installationId) { return owners.get(installationId) || null; },
    async claimRequest(hash, codeHash, uid, email, timestamp) {
      const item = requests.get(hash);
      if (!item || item.codeHash !== codeHash || item.status !== "pending") return null;
      Object.assign(item, { status: "claimed", ownerUid: uid, ownerEmail: email, claimedAt: timestamp });
      return structuredClone(item);
    },
    async applyClaim(installationId, uid) { owners.set(installationId, uid); },
    async lockExchange(hash, codeHash, lockId, timestamp) {
      const item = requests.get(hash);
      if (!item || item.codeHash !== codeHash || item.status !== "claimed") return null;
      Object.assign(item, { status: "exchanging", exchangeLock: lockId, exchangeStartedAt: timestamp });
      return structuredClone(item);
    },
    async bindDevice(installationId, uid, serialHash, timestamp) {
      bindings.set(uid, { installationId, serialHash, timestamp });
    },
    async completeExchange(hash, lockId, deviceUid, timestamp) {
      const item = requests.get(hash);
      if (!item || item.exchangeLock !== lockId) return false;
      Object.assign(item, { status: "exchanged", deviceUid, completedAt: timestamp, codeHash: null, rotationTokenHash: null });
      return true;
    },
    async rollbackExchange(hash, lockId, timestamp) {
      const item = requests.get(hash);
      if (item?.exchangeLock === lockId) Object.assign(item, { status: "claimed", exchangeLock: null, lastExchangeFailureAt: timestamp });
    }
  };
}

function deterministicBytes() {
  let value = 1;
  return size => Buffer.alloc(size, value++);
}

test("exchange returns a direct Firebase session and never a password or custom token", async () => {
  const store = storeFixture();
  const issued = [];
  const service = createEnrollmentService({
    store,
    now: () => Date.parse("2026-07-26T12:00:00Z"),
    randomBytes: deterministicBytes(),
    issueDeviceSession: async input => {
      issued.push(input);
      return {
        idToken: "header.payload.signature",
        refreshToken: "r".repeat(64),
        expiresIn: 3600,
        authenticationMethod: "server_generated_password_session"
      };
    }
  });

  const serial = "0000ABCD1234EF56";
  const request = await service.request({ serial, hostname: "etr-core" });
  await service.claim({
    serial,
    activationCode: request.activationCode,
    decodedUser: { uid: "owner-1", email: "owner@example.com", email_verified: true }
  });
  const result = await service.exchange({ serial, activationCode: request.activationCode });

  assert.equal(result.status, "exchanged");
  assert.equal(result.idToken, "header.payload.signature");
  assert.equal(result.refreshToken, "r".repeat(64));
  assert.equal(result.authenticationMethod, "server_generated_password_session");
  assert.equal(Object.hasOwn(result, "customToken"), false);
  assert.equal(Object.hasOwn(result, "password"), false);
  assert.equal(issued.length, 1);
  assert.match(issued[0].deviceUid, /^etrdev_[a-f0-9]{48}$/);
  assert.equal(issued[0].installationId, request.installationId);
  assert.equal(store.bindings.get(issued[0].deviceUid).installationId, request.installationId);
  assert.equal(store.requests.get(serialFingerprint(serial)).status, "exchanged");
});

test("a failed direct session rolls the exchange back to claimed", async () => {
  const store = storeFixture();
  const service = createEnrollmentService({
    store,
    now: () => Date.parse("2026-07-26T12:00:00Z"),
    randomBytes: deterministicBytes(),
    issueDeviceSession: async () => { throw new Error("identity unavailable"); }
  });
  const serial = "0000ABCD1234EF56";
  const request = await service.request({ serial });
  await service.claim({
    serial,
    activationCode: request.activationCode,
    decodedUser: { uid: "owner-1", email: "owner@example.com", email_verified: true }
  });
  await assert.rejects(service.exchange({ serial, activationCode: request.activationCode }), /identity unavailable/);
  assert.equal(store.requests.get(serialFingerprint(serial)).status, "claimed");
});
