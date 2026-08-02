import { WebSocket } from "ws";

export const GATEWAY_VERSION = "1.1.0";

export function buildGatewayHealth(
  devices,
  {
    version = GATEWAY_VERSION,
    revision = process.env.K_REVISION || null,
  } = {},
) {
  let viewers = 0;
  let readyViewers = 0;
  for (const device of devices.values()) {
    if (device.viewer?.readyState === WebSocket.OPEN) viewers += 1;
    if (device.viewer?.vncReady === true) readyViewers += 1;
  }
  return {
    ok: true,
    service: "etr-remote-gateway",
    version,
    revision,
    devices: devices.size,
    viewers,
    readyViewers,
    enrollment: "v1",
    admin: "v1",
  };
}

export function installGatewayHealthRoutes({ app, devices }) {
  const handler = (_req, res) => {
    res.setHeader("Cache-Control", "no-store");
    res.json(buildGatewayHealth(devices));
  };

  // `/healthz` is kept for local/Docker compatibility. Google Frontend has
  // returned its own 404 for this exact public path on the current service, so
  // all remote proofs use the namespaced route below.
  app.get("/healthz", handler);
  app.get("/api/health", handler);
}
