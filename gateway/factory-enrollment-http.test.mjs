import assert from "node:assert/strict";
import test from "node:test";
import { installEnrollmentRoutes } from "./enrollment-http.mjs";

function response() {
  return {
    statusCode: 200,
    headers: {},
    body: null,
    setHeader(name, value) { this.headers[name] = value; },
    status(value) { this.statusCode = value; return this; },
    json(value) { this.body = value; return this; }
  };
}

test("factory bootstrap binds a self-managed Firebase UID and returns its session", async () => {
  const routes = new Map();
  const updates = [];
  const app = { post(path, handler) { routes.set(path, handler); } };
  const db = {
    ref(path = "") {
      return {
        async update(value) { updates.push({ path, value }); }
      };
    }
  };
  const deviceBootstrap = {
    async verifyWorkflowToken() { return { run_id: "1" }; },
    async verifyDeviceRequest() { return { installationId: "etr-abcd1234ef56" }; },
    async register() { return {}; },
    async issueFactoryTicket() { return { status: "issued", ticket: "A".repeat(43) }; },
    async redeemFactoryTicket(input) {
      assert.equal(input.ticket, "A".repeat(43));
      assert.equal(input.serial, "0000ABCD1234EF56");
      return {
        status: "registered",
        installationId: "etr-abcd1234ef56",
        publicKeyFingerprint: "f".repeat(64),
        registeredAt: "2026-08-03T08:00:00.000Z"
      };
    }
  };
  const factorySessionIssuer = {
    async issue(input) {
      assert.deepEqual(input, { ticket: "A".repeat(43), serial: "0000ABCD1234EF56" });
      return {
        uid: "factory-device-uid",
        idToken: "header.payload.signature",
        refreshToken: "R".repeat(64),
        expiresIn: 3600,
        authMode: "factory_password_session",
        serialHash: "s".repeat(64)
      };
    }
  };
  const deviceSessionIssuer = {
    async issue() { throw new Error("not used"); },
    async health() { return { ok: true }; }
  };
  const auth = {
    async getUser(uid) { return { uid }; },
    async createUser(profile) { return profile; }
  };
  installEnrollmentRoutes({
    app,
    db,
    auth,
    verifyIdToken: async () => ({ uid: "factory" }),
    deviceBootstrap,
    deviceSessionIssuer,
    factorySessionIssuer,
    now: () => Date.parse("2026-08-03T08:00:00.000Z")
  });

  const res = response();
  await routes.get("/api/enrollment/factory-bootstrap")({
    headers: {},
    socket: { remoteAddress: "127.0.0.1" },
    body: {
      ticket: "A".repeat(43),
      serial: "0000ABCD1234EF56",
      installationId: "etr-abcd1234ef56",
      publicKey: "PUBLIC KEY",
      hostname: "etr-new"
    }
  }, res);

  assert.equal(res.statusCode, 201);
  assert.equal(res.body.installationId, "etr-abcd1234ef56");
  assert.equal(res.body.deviceUid, "factory-device-uid");
  assert.equal(res.body.idToken, "header.payload.signature");
  assert.equal(res.body.refreshToken, "R".repeat(64));
  assert.equal(res.body.authMode, "factory_password_session");
  assert.equal(res.headers["Cache-Control"], "no-store");
  assert.equal(updates.length, 1);
  assert.equal(updates[0].path, "");
  assert.equal(updates[0].value["deviceAccess/factory-device-uid"], "etr-abcd1234ef56");
  assert.equal(
    updates[0].value["installations/etr-abcd1234ef56/metadata/provisioning_mode"],
    "factory-ticket"
  );
});
