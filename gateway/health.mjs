import { WebSocket } from "ws";

export const GATEWAY_VERSION = "1.1.1";

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
    liveViewerDiagnostic: "github-oidc"
  };
}

export function installGatewayHealthRoutes({ app, devices }) {
  const handler = (_req, res) => {
    res.setHeader("Cache-Control", "no-store");
    res.json(buildGatewayHealth(devices));
  };

  app.get("/healthz", handler);
  app.get("/api/health", handler);
}
