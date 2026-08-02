import assert from "node:assert/strict";
import test from "node:test";
import { WebSocket } from "ws";

import { buildGatewayHealth, installGatewayHealthRoutes } from "./health.mjs";

test("builds a deterministic gateway health payload", () => {
  const devices = new Map([
    ["etr-one", { viewer: { readyState: WebSocket.OPEN, vncReady: true } }],
    ["etr-two", { viewer: { readyState: WebSocket.CLOSED, vncReady: false } }],
  ]);
  assert.deepEqual(
    buildGatewayHealth(devices, { version: "test", revision: "revision-42" }),
    {
      ok: true,
      service: "etr-remote-gateway",
      version: "test",
      revision: "revision-42",
      devices: 2,
      viewers: 1,
      readyViewers: 1,
      enrollment: "v1",
      admin: "v1",
    },
  );
});

test("installs local and public health routes", () => {
  const routes = [];
  const app = { get(path, handler) { routes.push({ path, handler }); } };
  installGatewayHealthRoutes({ app, devices: new Map() });
  assert.deepEqual(routes.map((route) => route.path), ["/healthz", "/api/health"]);

  const headers = new Map();
  let payload;
  routes[1].handler({}, {
    setHeader(name, value) { headers.set(name, value); },
    json(value) { payload = value; },
  });
  assert.equal(headers.get("Cache-Control"), "no-store");
  assert.equal(payload.ok, true);
  assert.equal(payload.service, "etr-remote-gateway");
  assert.equal(payload.devices, 0);
});
