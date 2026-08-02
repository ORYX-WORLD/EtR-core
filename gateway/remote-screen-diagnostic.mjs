import { WebSocket } from "ws";

function diagnosticError(message, status, code, cause) {
  return Object.assign(new Error(message, { cause }), { status, code });
}

function bearer(req) {
  const value = String(req.headers.authorization || "");
  return value.startsWith("Bearer ") ? value.slice(7) : "";
}

function safeDiagnosticError(res, error) {
  const status = Number(error?.status || 500);
  const code = String(error?.code || "remote-screen-diagnostic-error");
  if (status >= 500) {
    console.error("Remote screen diagnostic ticket failed", {
      name: error?.name || "Error",
      code,
      message: String(error?.message || error || "unknown").slice(0, 400)
    });
  }
  return res.status(status).json({
    error: status >= 500 ? "Diagnostic écran EtR indisponible" : error.message,
    code
  });
}

export function installRemoteScreenDiagnosticRoute({
  app,
  deviceBootstrap,
  getDevice,
  issueViewerTicket,
  gatewayOrigin = process.env.PUBLIC_GATEWAY_ORIGIN || "",
  ticketTtlMs = 45_000
} = {}) {
  if (!app?.post) throw new Error("Remote screen diagnostic route requires Express app");
  if (!deviceBootstrap?.verifyWorkflowToken) {
    throw new Error("Remote screen diagnostic route requires GitHub OIDC verification");
  }
  if (typeof getDevice !== "function" || typeof issueViewerTicket !== "function") {
    throw new Error("Remote screen diagnostic route requires device and ticket callbacks");
  }

  // Cloud Run termine TLS avant Express et transmet le protocole public dans
  // X-Forwarded-Proto. Cette confiance limitée au premier proxy corrige aussi
  // l'URL viewer renvoyée par la route client normale, qui utilise req.protocol.
  if (typeof app.set === "function") app.set("trust proxy", 1);

  app.post("/api/diagnostics/remote-screen-ticket", async (req, res) => {
    try {
      const claims = await deviceBootstrap.verifyWorkflowToken(bearer(req));
      const installationId = String(req.body?.installationId || "").trim();
      if (!/^[A-Za-z0-9._-]{2,80}$/.test(installationId)) {
        throw diagnosticError(
          "Installation EtR invalide",
          400,
          "remote-screen-diagnostic/installation-invalid"
        );
      }

      const device = getDevice(installationId);
      if (!device || device.readyState !== WebSocket.OPEN) {
        throw diagnosticError(
          "EtR hors ligne",
          409,
          "remote-screen-diagnostic/device-offline"
        );
      }

      const ticket = issueViewerTicket({
        installationId,
        uid: `github-actions:${String(claims.run_id || "unknown")}`,
        ttlMs: ticketTtlMs
      });
      const origin = String(gatewayOrigin || `${req.protocol}://${req.get("host")}`).replace(/\/$/, "");
      res.setHeader("Cache-Control", "no-store");
      return res.json({
        ok: true,
        installationId,
        viewerUrl: `${origin}/viewer?ticket=${encodeURIComponent(ticket)}`,
        expiresIn: Math.floor(ticketTtlMs / 1000),
        deviceConnected: true
      });
    } catch (error) {
      return safeDiagnosticError(res, error);
    }
  });
}
