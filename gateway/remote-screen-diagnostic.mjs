import crypto from "node:crypto";
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
    console.error("Remote screen diagnostic failed", {
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

function validateInstallationId(value) {
  const installationId = String(value || "").trim();
  if (!/^[A-Za-z0-9._-]{2,80}$/.test(installationId)) {
    throw diagnosticError(
      "Installation EtR invalide",
      400,
      "remote-screen-diagnostic/installation-invalid"
    );
  }
  return installationId;
}

function requireOnlineDevice(getDevice, installationId) {
  const device = getDevice(installationId);
  if (!device || device.readyState !== WebSocket.OPEN) {
    throw diagnosticError(
      "EtR hors ligne",
      409,
      "remote-screen-diagnostic/device-offline"
    );
  }
  return device;
}

function requestDeviceReport(device, { timeoutMs = 15_000 } = {}) {
  const requestId = crypto.randomBytes(12).toString("base64url");
  return new Promise((resolve, reject) => {
    let settled = false;
    const cleanup = () => {
      device.off?.("message", onMessage);
      clearTimeout(timer);
    };
    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      cleanup();
      fn(value);
    };
    const onMessage = (data, isBinary) => {
      if (isBinary) return;
      let message;
      try {
        message = JSON.parse(data.toString());
      } catch {
        return;
      }
      if (message?.type !== "diagnostic-result" || message?.requestId !== requestId) return;
      finish(resolve, message.report || {});
    };
    const timer = setTimeout(() => {
      finish(
        reject,
        diagnosticError(
          "Le Raspberry n'a pas répondu au diagnostic matériel",
          504,
          "remote-screen-diagnostic/device-report-timeout"
        )
      );
    }, timeoutMs);

    device.on?.("message", onMessage);
    device.send(
      JSON.stringify({ type: "diagnostic", requestId }),
      (error) => {
        if (!error) return;
        finish(
          reject,
          diagnosticError(
            "Commande de diagnostic non transmise au Raspberry",
            502,
            "remote-screen-diagnostic/device-report-send-failed",
            error
          )
        );
      }
    );
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

  if (typeof app.set === "function") app.set("trust proxy", 1);

  app.post("/api/diagnostics/remote-screen-ticket", async (req, res) => {
    try {
      const claims = await deviceBootstrap.verifyWorkflowToken(bearer(req));
      const installationId = validateInstallationId(req.body?.installationId);
      requireOnlineDevice(getDevice, installationId);

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

  app.post("/api/diagnostics/device-report", async (req, res) => {
    try {
      await deviceBootstrap.verifyWorkflowToken(bearer(req));
      const installationId = validateInstallationId(req.body?.installationId);
      const device = requireOnlineDevice(getDevice, installationId);
      const report = await requestDeviceReport(device);
      res.setHeader("Cache-Control", "no-store");
      return res.json({ ok: true, installationId, report });
    } catch (error) {
      return safeDiagnosticError(res, error);
    }
  });
}
