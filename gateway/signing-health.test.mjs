import assert from "node:assert/strict";
import test from "node:test";
import { installEnrollmentRoutes } from "./enrollment-http.mjs";

function fixture({ signingError = null } = {}) {
  const routes = new Map();
  const app = { post(path, handler) { routes.set(path, handler); } };
  const claimsSeen = [];
  const deviceBootstrap = {
    async verifyWorkflowToken(token) {
      assert.equal(token, "github-oidc-token");
      return { run_id: "123456789", repository: "ORYX-WORLD/EtR-core" };
    },
    async verifyDeviceRequest() { return { installationId: "etr-test" }; },
    async register() { return {}; }
  };
  const auth = {
    async createCustomToken(uid, claims) {
      claimsSeen.push({ uid, claims });
      if (signingError) throw signingError;
      return "header.payload.signature";
    }
  };
  const db = { ref() { return {}; } };
  installEnrollmentRoutes({ app, db, auth, verifyIdToken: async () => ({ uid: "owner" }), deviceBootstrap });
  return { routes, claimsSeen };
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

test("signing health is protected by GitHub OIDC and never exposes the custom token", async () => {
  const { routes, claimsSeen } = fixture();
  const handler = routes.get("/api/enrollment/signing-health");
  assert.equal(typeof handler, "function");
  const res = response();
  await handler({ headers: { authorization: "Bearer github-oidc-token" }, socket: { remoteAddress: "127.0.0.1" }, body: {} }, res);
  assert.equal(res.statusCode, 200);
  assert.deepEqual(res.body, { ok: true, signer: "firebase-admin", algorithm: "RS256" });
  assert.equal(JSON.stringify(res.body).includes("header.payload.signature"), false);
  assert.match(claimsSeen[0].uid, /^etrhealth_[a-f0-9]{32}$/);
  assert.deepEqual(claimsSeen[0].claims, { etrSigningHealth: true, workflowRunId: "123456789" });
  assert.equal(res.headers["Cache-Control"], "no-store");
});

test("signing failure is returned as a generic server error", async () => {
  const { routes } = fixture({ signingError: Object.assign(new Error("iam.serviceAccounts.signBlob denied"), { code: "auth/insufficient-permission" }) });
  const res = response();
  await routes.get("/api/enrollment/signing-health")({ headers: { authorization: "Bearer github-oidc-token" }, socket: {}, body: {} }, res);
  assert.equal(res.statusCode, 500);
  assert.deepEqual(res.body, { error: "Erreur d’activation EtR", code: "enrollment_error" });
  assert.equal(JSON.stringify(res.body).includes("signBlob"), false);
});
