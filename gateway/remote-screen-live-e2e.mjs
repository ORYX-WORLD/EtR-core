import { writeFile } from "node:fs/promises";
import process from "node:process";
import { WebSocket } from "ws";

const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

function requiredEnvironment(name) {
  const value = String(process.env[name] || "").trim();
  if (!value) throw new Error(`${name}_missing`);
  return value;
}

async function issueDiagnosticTicket({ gatewayOrigin, oidcToken, installationId }) {
  let lastResponse;
  for (let attempt = 1; attempt <= 60; attempt += 1) {
    const response = await fetch(`${gatewayOrigin}/api/diagnostics/remote-screen-ticket`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${oidcToken}`,
        "content-type": "application/json",
        accept: "application/json",
        "user-agent": "EtR-Live-Remote-Screen-E2E/1.0"
      },
      body: JSON.stringify({ installationId }),
      cache: "no-store"
    });
    const body = await response.json().catch(() => ({}));
    lastResponse = { status: response.status, body };
    if (response.ok) return body;
    if (response.status !== 409) {
      throw new Error(`diagnostic_ticket_http_${response.status}:${String(body.code || "unknown")}`);
    }
    await sleep(2_000);
  }
  throw new Error(`device_not_connected:${JSON.stringify(lastResponse)}`);
}

async function verifyViewerAssets(viewerUrl, expectedOrigin) {
  const parsed = new URL(viewerUrl);
  if (parsed.origin !== expectedOrigin || parsed.pathname !== "/viewer" || !parsed.searchParams.get("ticket")) {
    throw new Error("viewer_url_invalid");
  }

  const response = await fetch(parsed, {
    headers: { accept: "text/html", "user-agent": "EtR-Live-Remote-Screen-E2E/1.0" },
    cache: "no-store"
  });
  const html = await response.text();
  if (!response.ok) throw new Error(`viewer_http_${response.status}`);
  if (!/no-store/i.test(response.headers.get("cache-control") || "")) {
    throw new Error("viewer_cache_policy_invalid");
  }
  for (const marker of [
    'import RFB from "/novnc/rfb-browser-v2.js"',
    'new RFB(document.getElementById("screen"),url',
    "Connexion sécurisée à l’EtR"
  ]) {
    if (!html.includes(marker)) throw new Error(`viewer_marker_missing:${marker}`);
  }

  const bundleUrl = new URL("/novnc/rfb-browser-v2.js", parsed.origin);
  const bundle = await fetch(bundleUrl, {
    headers: { accept: "text/javascript", "user-agent": "EtR-Live-Remote-Screen-E2E/1.0" }
  });
  const bundleBytes = Buffer.from(await bundle.arrayBuffer());
  if (!bundle.ok || bundleBytes.length < 10_000) {
    throw new Error(`novnc_bundle_invalid:${bundle.status}:${bundleBytes.length}`);
  }
  return {
    viewerStatus: response.status,
    viewerCacheControl: response.headers.get("cache-control") || "",
    noVncBundleStatus: bundle.status,
    noVncBundleBytes: bundleBytes.length,
    ticket: parsed.searchParams.get("ticket")
  };
}

async function verifyVncBanner({ gatewayOrigin, ticket, allowedOrigin }) {
  const websocketUrl = new URL(gatewayOrigin);
  websocketUrl.protocol = websocketUrl.protocol === "https:" ? "wss:" : "ws:";
  websocketUrl.pathname = "/client";
  websocketUrl.search = new URLSearchParams({ ticket }).toString();

  return await new Promise((resolve, reject) => {
    const websocket = new WebSocket(websocketUrl, { origin: allowedOrigin });
    let opened = false;
    const timeout = setTimeout(() => {
      websocket.terminate();
      reject(new Error("vnc_banner_timeout"));
    }, 25_000);

    websocket.once("open", () => {
      opened = true;
    });
    websocket.on("message", (data, isBinary) => {
      if (!isBinary) return;
      const bytes = Buffer.from(data);
      const prefix = bytes.subarray(0, 12).toString("ascii");
      if (!prefix.startsWith("RFB ")) {
        clearTimeout(timeout);
        websocket.close(1000, "unexpected banner");
        reject(new Error(`vnc_banner_invalid:${JSON.stringify(prefix)}`));
        return;
      }
      clearTimeout(timeout);
      websocket.close(1000, "diagnostic complete");
      resolve({
        websocketOpened: opened,
        websocketStatus: 101,
        vncBanner: prefix.trim(),
        firstPayloadBytes: bytes.length
      });
    });
    websocket.once("unexpected-response", (_request, response) => {
      clearTimeout(timeout);
      reject(new Error(`viewer_websocket_http_${response.statusCode || 0}`));
    });
    websocket.once("error", error => {
      clearTimeout(timeout);
      reject(error);
    });
  });
}

async function main() {
  const gatewayOrigin = new URL(requiredEnvironment("GATEWAY_URL")).origin;
  const oidcToken = requiredEnvironment("GITHUB_OIDC_TOKEN");
  const installationId = String(process.env.ETR_INSTALLATION_ID || "etr-core").trim();
  const allowedOrigin = String(process.env.ETR_VIEWER_ORIGIN || "https://oryx-froid-industriel.web.app").trim();
  const outputPath = String(process.env.ETR_E2E_OUTPUT || "").trim();

  const ticketResponse = await issueDiagnosticTicket({ gatewayOrigin, oidcToken, installationId });
  if (ticketResponse.installationId !== installationId || ticketResponse.deviceConnected !== true) {
    throw new Error("diagnostic_ticket_payload_invalid");
  }
  const assets = await verifyViewerAssets(ticketResponse.viewerUrl, gatewayOrigin);
  const websocket = await verifyVncBanner({
    gatewayOrigin,
    ticket: assets.ticket,
    allowedOrigin
  });

  const report = {
    ok: true,
    checkedAt: new Date().toISOString(),
    gatewayOrigin,
    installationId,
    diagnosticTicketIssued: true,
    viewerStatus: assets.viewerStatus,
    viewerCacheControl: assets.viewerCacheControl,
    noVncBundleStatus: assets.noVncBundleStatus,
    noVncBundleBytes: assets.noVncBundleBytes,
    websocketOpened: websocket.websocketOpened,
    websocketStatus: websocket.websocketStatus,
    vncBanner: websocket.vncBanner,
    firstPayloadBytes: websocket.firstPayloadBytes
  };
  const serialized = `${JSON.stringify(report, null, 2)}\n`;
  if (outputPath) await writeFile(outputPath, serialized, "utf8");
  process.stdout.write(serialized);
}

main().catch(error => {
  const report = {
    ok: false,
    checkedAt: new Date().toISOString(),
    error: error instanceof Error ? error.name : "Error",
    message: String(error?.message || error || "unknown").slice(0, 800)
  };
  process.stderr.write(`${JSON.stringify(report, null, 2)}\n`);
  process.exitCode = 1;
});
