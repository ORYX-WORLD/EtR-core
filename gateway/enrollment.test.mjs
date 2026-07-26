import assert from "node:assert/strict";
import test from "node:test";
import {
  ENROLLMENT_POLICY,
  EnrollmentError,
  createEnrollmentService,
  deriveDeviceUid,
  deriveInstallationId,
  formatActivationCode,
  generateActivationCode,
  normalizeActivationCode,
  normalizeSerial,
  serialFingerprint
} from "./enrollment.mjs";

function deterministicBytes() {
  let sequence = 0;
  return size => Buffer.alloc(size, sequence++ % 32);
}

function memoryStore() {
  const state = {
    requests: new Map(),
    owners: new Map(),
    claims: [],
    devices: new Map(),
    failedAttempts: 0
  };
  return {
    state,
    async getRequest(serialHash) { return state.requests.get(serialHash) || null; },
    async putRequest(serialHash, request) { state.requests.set(serialHash, structuredClone(request)); },
    async incrementAttempts(serialHash, timestamp) {
      const current = state.requests.get(serialHash);
      current.attempts = Number(current.attempts || 0) + 1;
      current.lastAttemptAt = timestamp;
      state.failedAttempts += 1;
    },
    async getOwner(installationId) { return state.owners.get(installationId) || null; },
    async claimRequest(serialHash, codeHash, ownerUid, ownerEmail, timestamp) {
      const current = state.requests.get(serialHash);
      if (!current || current.codeHash !== codeHash) return null;
      if (current.status === "claimed" && current.ownerUid === ownerUid) return structuredClone(current);
      if (current.status !== "pending") return null;
      Object.assign(current, { status: "claimed", ownerUid, ownerEmail, claimedAt: timestamp });
      return structuredClone(current);
    },
    async applyClaim(installationId, ownerUid, ownerEmail, timestamp) {
      state.owners.set(installationId, ownerUid);
      state.claims.push({ installationId, ownerUid, ownerEmail, timestamp });
    },
    async lockExchange(serialHash, codeHash, lockId, timestamp) {
      const current = state.requests.get(serialHash);
      if (!current || current.codeHash !== codeHash || current.status !== "claimed" || !current.ownerUid) return null;
      Object.assign(current, { status: "exchanging", exchangeLock: lockId, exchangeStartedAt: timestamp });
      return structuredClone(current);
    },
    async bindDevice(installationId, deviceUid, serialHash, timestamp) {
      state.devices.set(deviceUid, { installationId, serialHash, timestamp });
    },
    async completeExchange(serialHash, lockId, deviceUid, timestamp) {
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

function memoryAuth() {
  const users = new Map();
  return {
    users,
    async getUser(uid) {
      if (!users.has(uid)) throw Object.assign(new Error("not found"), { code: "auth/user-not-found" });
      return users.get(uid);
    },
    async createUser(user) { users.set(user.uid, user); return user; },
    async createCustomToken(uid, claims) { return `custom.${uid}.${Buffer.from(JSON.stringify(claims)).toString("base64url")}`; }
  };
}

function serviceFixture() {
  const store = memoryStore();
  const auth = memoryAuth();
  let clock = Date.parse("2026-07-26T10:00:00Z");
  const service = createEnrollmentService({
    store,
    auth,
    now: () => clock,
    randomBytes: deterministicBytes()
  });
  return { store, auth, service, advance: milliseconds => { clock += milliseconds; } };
}

test("normalizes the Raspberry serial and derives stable identifiers", () => {
  const serial = normalizeSerial(" 0000-abcd-1234-ef56 ");
  assert.equal(serial, "0000ABCD1234EF56");
  assert.equal(deriveInstallationId(serial), "etr-abcd1234ef56");
  const fingerprint = serialFingerprint(serial);
  assert.match(fingerprint, /^[a-f0-9]{64}$/);
  assert.equal(deriveDeviceUid(fingerprint), `etrdev_${fingerprint.slice(0, 48)}`);
});

test("generates a human-readable activation code with the declared entropy policy", () => {
  const code = generateActivationCode(size => Buffer.alloc(size, 3));
  assert.equal(code.length, 20);
  assert.equal(formatActivationCode(code).split("-").length, 4);
  assert.equal(normalizeActivationCode(formatActivationCode(code)), code);
  assert.equal(ENROLLMENT_POLICY.activationLength, 20);
  assert.equal(ENROLLMENT_POLICY.activationBits, 100);
  assert.equal(ENROLLMENT_POLICY.requestTtlSeconds, 86400);
});

test("creates a request without storing the activation code or rotation token in clear text", async () => {
  const { service, store } = serviceFixture();
  const result = await service.request({ serial: "0000ABCD1234EF56", hostname: "etr-core" });
  assert.equal(result.installationId, "etr-abcd1234ef56");
  assert.match(result.activationCode, /^[A-Z0-9]{5}(?:-[A-Z0-9]{5}){3}$/);
  assert.ok(result.rotationToken.length > 30);
  const stored = store.state.requests.get(serialFingerprint("0000ABCD1234EF56"));
  assert.equal(stored.status, "pending");
  assert.match(stored.codeHash, /^[a-f0-9]{64}$/);
  assert.match(stored.rotationTokenHash, /^[a-f0-9]{64}$/);
  assert.equal(JSON.stringify(stored).includes(result.activationCode.replaceAll("-", "")), false);
  assert.equal(JSON.stringify(stored).includes(result.rotationToken), false);
});

test("protects an active request against unauthorized rotation", async () => {
  const { service } = serviceFixture();
  const first = await service.request({ serial: "0000ABCD1234EF56" });
  await assert.rejects(
    service.request({ serial: "0000ABCD1234EF56" }),
    error => error instanceof EnrollmentError && error.code === "request_exists"
  );
  const rotated = await service.request({ serial: "0000ABCD1234EF56", rotationToken: first.rotationToken });
  assert.notEqual(rotated.activationCode, first.activationCode);
  assert.notEqual(rotated.rotationToken, first.rotationToken);
});

test("refuses an invalid code and counts the failed attempt", async () => {
  const { service, store } = serviceFixture();
  await service.request({ serial: "0000ABCD1234EF56" });
  await assert.rejects(
    service.claim({
      serial: "0000ABCD1234EF56",
      activationCode: "22222-22222-22222-22223",
      decodedUser: { uid: "owner-1", email: "owner@example.com", email_verified: true }
    }),
    error => error instanceof EnrollmentError && error.code === "activation_refused"
  );
  assert.equal(store.state.failedAttempts, 1);
});

test("requires a verified Firebase account before claiming an EtR", async () => {
  const { service } = serviceFixture();
  const request = await service.request({ serial: "0000ABCD1234EF56" });
  await assert.rejects(
    service.claim({
      serial: "0000ABCD1234EF56",
      activationCode: request.activationCode,
      decodedUser: { uid: "owner-1", email: "owner@example.com", email_verified: false }
    }),
    error => error instanceof EnrollmentError && error.code === "email_not_verified"
  );
});

test("claims an EtR idempotently for one owner and creates the owner membership", async () => {
  const { service, store } = serviceFixture();
  const request = await service.request({ serial: "0000ABCD1234EF56" });
  const user = { uid: "owner-1", email: "owner@example.com", email_verified: true };
  const first = await service.claim({ serial: "0000ABCD1234EF56", activationCode: request.activationCode, decodedUser: user });
  const second = await service.claim({ serial: "0000ABCD1234EF56", activationCode: request.activationCode, decodedUser: user });
  assert.deepEqual(first, second);
  assert.equal(first.role, "owner");
  assert.equal(store.state.owners.get(first.installationId), "owner-1");
  assert.equal(store.state.claims.length, 2);
});

test("exchanges a claimed code once for a technical Firebase identity", async () => {
  const { service, store, auth } = serviceFixture();
  const serial = "0000ABCD1234EF56";
  const request = await service.request({ serial });
  await service.claim({
    serial,
    activationCode: request.activationCode,
    decodedUser: { uid: "owner-1", email: "owner@example.com", email_verified: true }
  });
  const exchange = await service.exchange({ serial, activationCode: request.activationCode });
  assert.equal(exchange.status, "exchanged");
  assert.equal(exchange.installationId, request.installationId);
  assert.match(exchange.deviceUid, /^etrdev_[a-f0-9]{48}$/);
  assert.ok(exchange.customToken.startsWith(`custom.${exchange.deviceUid}.`));
  assert.ok(auth.users.has(exchange.deviceUid));
  assert.equal(store.state.devices.get(exchange.deviceUid).installationId, request.installationId);
  assert.equal(store.state.requests.get(serialFingerprint(serial)).status, "exchanged");
  await assert.rejects(
    service.exchange({ serial, activationCode: request.activationCode }),
    error => error instanceof EnrollmentError && ["invalid_activation_code", "activation_refused", "exchange_unavailable"].includes(error.code)
  );
});

test("does not issue a device identity before the client claim", async () => {
  const { service } = serviceFixture();
  const request = await service.request({ serial: "0000ABCD1234EF56" });
  await assert.rejects(
    service.exchange({ serial: "0000ABCD1234EF56", activationCode: request.activationCode }),
    error => error instanceof EnrollmentError && error.code === "awaiting_claim"
  );
});

test("expires pending activation requests after 24 hours", async () => {
  const { service, advance } = serviceFixture();
  const request = await service.request({ serial: "0000ABCD1234EF56" });
  advance(ENROLLMENT_POLICY.requestTtlSeconds * 1000 + 1);
  await assert.rejects(
    service.claim({
      serial: "0000ABCD1234EF56",
      activationCode: request.activationCode,
      decodedUser: { uid: "owner-1", email: "owner@example.com", email_verified: true }
    }),
    error => error instanceof EnrollmentError && error.code === "request_expired"
  );
});
