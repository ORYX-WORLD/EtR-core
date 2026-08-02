import assert from "node:assert/strict";
import test from "node:test";
import { WebSocket } from "ws";

import { installRemoteScreenDiagnosticRoute } from "./remote-screen-diagnostic.mjs";

function createHarness({ device, verifyError, gatewayOrigin = "https://gateway.example" } = {}) {
  const routes = new Map();
  const issued = [];
  const settings = new Map();
  const app = {
    set(name, value) { settings.set(name, value); },
    post(path, handler) { routes.set(path, handler); }
  };
  const deviceBootstrap = {
    async verifyWorkflowToken(token) {
      if (verifyError) throw verifyError;
      assert.equal(token, "github-oidc-token");
      return { run_id: "12345", repository: "ORYX-WORLD/EtR-core" };
    }
  };
  installRemoteScreenDiagnosticRoute({
    app,
    deviceBootstrap,
    getDevice: () => device,
    issueViewerTicket: (value) => {
      issued.push(value);
      return "ticket-value";
    },
    gatewayOrigin,
    ticketTtlMs: 45_000
  });
  return {
    handler: routes.get("/api/diagnostics/remote-screen-ticket"),
    issued,
    settings
  };
}

function responseHarness() {
  const headers = new Map();
  const result = { status: 200, body: null };
  return {
    result,
    response: {
      setHeader(name, value) { headers.set(name, value); },
      status(value) { result.status = value; return this; },
      json(value) { result.body = value; result.headers = headers; return value; }
    }
  };
}

test("configures Express to trust the first Cloud Run proxy", () => {
  const { settings } = createHarness({ device: { readyState: WebSocket.OPEN } });
  assert.equal(settings.get("trust proxy"), 1);
});

test("issues a short-lived viewer ticket only after GitHub OIDC verification", async () => {
  const { handler, issued } = createHarness({
    device: { readyState: WebSocket.OPEN }
  });
  const { response, result } = responseHarness();
  await handler({
    headers: { authorization: "Bearer github-oidc-token" },
    body: { installationId: "etr-core" },
    protocol: "https",
    get: () => "gateway.example"
  }, response);

  assert.equal(result.status, 200);
  assert.equal(result.body.ok, true);
  assert.equal(result.body.installationId, "etr-core");
  assert.equal(result.body.deviceConnected, true);
  assert.equal(result.body.expiresIn, 45);
  assert.match(result.body.viewerUrl, /^https:\/\/gateway\.example\/viewer\?ticket=/);
  assert.equal(result.headers.get("Cache-Control"), "no-store");
  assert.deepEqual(issued, [{
    installationId: "etr-core",
    uid: "github-actions:12345",
    ttlMs: 45_000
  }]);
});

test("uses the trusted public protocol when no explicit gateway origin is configured", async () => {
  const { handler } = createHarness({
    device: { readyState: WebSocket.OPEN },
    gatewayOrigin: ""
  });
  const { response, result } = responseHarness();
  await handler({
    headers: { authorization: "Bearer github-oidc-token" },
    body: { installationId: "etr-core" },
    protocol: "https",
    get: () => "etr-remote-gateway.example.run.app"
  }, response);
  assert.match(
    result.body.viewerUrl,
    /^https:\/\/etr-remote-gateway\.example\.run\.app\/viewer\?ticket=/
  );
});

test("refuses a diagnostic ticket while the EtR is offline", async () => {
  const { handler, issued } = createHarness({ device: null });
  const { response, result } = responseHarness();
  await handler({
    headers: { authorization: "Bearer github-oidc-token" },
    body: { installationId: "etr-core" },
    protocol: "https",
    get: () => "gateway.example"
  }, response);
  assert.equal(result.status, 409);
  assert.equal(result.body.code, "remote-screen-diagnostic/device-offline");
  assert.equal(issued.length, 0);
});

test("preserves GitHub OIDC authorization failures", async () => {
  const error = Object.assign(new Error("Jeton de déploiement invalide"), {
    status: 401,
    code: "github_oidc_invalid"
  });
  const { handler } = createHarness({
    device: { readyState: WebSocket.OPEN },
    verifyError: error
  });
  const { response, result } = responseHarness();
  await handler({
    headers: { authorization: "Bearer invalid" },
    body: { installationId: "etr-core" },
    protocol: "https",
    get: () => "gateway.example"
  }, response);
  assert.equal(result.status, 401);
  assert.equal(result.body.code, "github_oidc_invalid");
});
