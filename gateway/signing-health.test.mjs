import assert from "node:assert/strict";
import test from "node:test";
import { installEnrollmentRoutes } from "./enrollment-http.mjs";

function fixture({ healthError = null } = {}) {
  const routes = new Map();
  const app = { post(path, handler) { routes.set(path, handler); } };
  const healthCalls = [];
  const deviceBootstrap = {
    async verifyWorkflowToken(token) {
      assert.equal(token, "github-oidc-token");
      return { run_id: "123456789", repository: "ORYX-WORLD/EtR-core" };
    },
    async verifyDeviceRequest() { return { installationId: "etr-test" }; },
    async register() { return {}; }
  };
  const deviceSessionIssuer = {
    async issue(uid, claims) {
      return { idToken: "header.payload.signature", refreshToken: "r".repeat(80), expiresIn: 3600, authMode: "password_session", uid, claims };
    },
    async health(runId) {
      healthCalls.push(runId);
      if (healthError) throw healthError;
      return { ok: true, mode: "firebase-password-session", tokenExchange: true };
    }
  };
  const auth = {
    async getUser(uid) { return { uid }; },
    async createUser(profile) { return profile; }
  };
  const db = { ref() { return {}; } };
  installEnrollmentRoutes({ app, db, auth, verifyIdToken: async () => ({ uid: "owner" }), deviceBootstrap, deviceSessionIssuer });
  return { routes, healthCalls };
}

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

test("session health is protected by GitHub OIDC and never exposes Firebase tokens", async () => {
  const { routes, healthCalls } = fixture();
  const handler = routes.get("/api/enrollment/session-health");
  assert.equal(typeof handler, "function");
  const res = response();
  await handler({ headers: { authorization: "Bearer github-oidc-token" }, socket: { remoteAddress: "127.0.0.1" }, body: {} }, res);
  assert.equal(res.statusCode, 200);
  assert.deepEqual(res.body, { ok: true, mode: "firebase-password-session", tokenExchange: true });
  assert.deepEqual(healthCalls, ["123456789"]);
  assert.equal(JSON.stringify(res.body).includes("Token"), true);
  assert.equal(JSON.stringify(res.body).includes("header.payload.signature"), false);
  assert.equal(JSON.stringify(res.body).includes("refreshToken"), false);
  assert.equal(res.headers["Cache-Control"], "no-store");
});

test("session issuance failure is returned as a generic server error", async () => {
  const { routes } = fixture({ healthError: Object.assign(new Error("SENSITIVE_AUTH_PROVIDER_DETAIL"), { code: "auth/device-session-unavailable" }) });
  const res = response();
  await routes.get("/api/enrollment/session-health")({ headers: { authorization: "Bearer github-oidc-token" }, socket: {}, body: {} }, res);
  assert.equal(res.statusCode, 500);
  assert.deepEqual(res.body, { error: "Erreur d’activation EtR", code: "enrollment_error" });
  assert.equal(JSON.stringify(res.body).includes("SENSITIVE"), false);
});
