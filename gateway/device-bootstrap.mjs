import crypto from "node:crypto";
import { createRemoteJWKSet, jwtVerify } from "jose";
import { EnrollmentError, deriveInstallationId, normalizeSerial, serialFingerprint } from "./enrollment.mjs";

const GITHUB_ISSUER = "https://token.actions.githubusercontent.com";
const GITHUB_JWKS = createRemoteJWKSet(new URL("https://token.actions.githubusercontent.com/.well-known/jwks"), {
  cooldownDuration: 30_000,
  timeoutDuration: 10_000
});
const SIGNATURE_WINDOW_SECONDS = 5 * 60;
const NONCE_TTL_MS = 10 * 60 * 1000;

function publicKeyDetails(publicKeyPem) {
  const pem = String(publicKeyPem || "").trim() + "\n";
  let key;
  try {
    key = crypto.createPublicKey(pem);
  } catch {
    throw new EnrollmentError(400, "invalid_device_key", "Clé publique de l’EtR invalide");
  }
  if (key.asymmetricKeyType !== "ed25519") {
    throw new EnrollmentError(400, "invalid_device_key", "La clé de l’EtR doit utiliser Ed25519");
  }
  const der = key.export({ format: "der", type: "spki" });
  return {
    key,
    pem,
    fingerprint: crypto.createHash("sha256").update(der).digest("hex")
  };
}

function canonicalRequest({ action, serial, activationCode, hostname, rotationToken, timestamp, nonce }) {
  const body = JSON.stringify({
    action: String(action || ""),
    activationCode: String(activationCode || ""),
    hostname: String(hostname || ""),
    rotationToken: String(rotationToken || ""),
    serial: String(serial || "")
  });
  return Buffer.from(`${timestamp}\n${nonce}\n${body}`, "utf8");
}

function bearer(req) {
  const value = String(req.headers.authorization || "");
  return value.startsWith("Bearer ") ? value.slice(7) : "";
}

function clientError(status, code, message) {
  return new EnrollmentError(status, code, message);
}

function safeJsonError(res, error) {
  if (error instanceof EnrollmentError) {
    return res.status(error.status).json({ error: error.message, code: error.code });
  }
  console.error("Device bootstrap failed", {
    name: error?.name || "Error",
    code: error?.code || "",
    message: String(error?.message || error || "unknown").slice(0, 400)
  });
  return res.status(500).json({ error: "Erreur d’identité EtR", code: "device_bootstrap_error" });
}

export function createDeviceBootstrapService({
  db,
  now = () => Date.now(),
  githubRepository = process.env.ETR_GITHUB_REPOSITORY || "ORYX-WORLD/EtR-core",
  githubJwks = GITHUB_JWKS
}) {
  if (!db?.ref) throw new Error("Device bootstrap requires Firebase Database");
  const usedNonces = new Map();

  function cleanupNonces(timestamp) {
    if (usedNonces.size < 500) return;
    for (const [key, expiresAt] of usedNonces) if (expiresAt <= timestamp) usedNonces.delete(key);
  }

  async function verifyWorkflowToken(token) {
    if (!token) throw clientError(401, "github_oidc_missing", "Jeton de déploiement manquant");
    let payload;
    try {
      ({ payload } = await jwtVerify(token, githubJwks, {
        algorithms: ["RS256"],
        issuer: GITHUB_ISSUER,
        audience: "etr-bootstrap",
        requiredClaims: ["sub", "repository", "ref", "run_id", "sha"]
      }));
    } catch {
      throw clientError(401, "github_oidc_invalid", "Jeton de déploiement invalide");
    }
    if (payload.repository !== githubRepository) {
      throw clientError(403, "github_repository_refused", "Dépôt GitHub non autorisé");
    }
    if (payload.ref !== "refs/heads/main") {
      throw clientError(403, "github_ref_refused", "Seule la branche principale peut enregistrer un EtR");
    }
    if (!["push", "workflow_dispatch"].includes(String(payload.event_name || ""))) {
      throw clientError(403, "github_event_refused", "Événement GitHub non autorisé");
    }
    return payload;
  }

  async function register({ token, serial, installationId, publicKeyPem } = {}) {
    const claims = await verifyWorkflowToken(token);
    const normalizedSerial = normalizeSerial(serial);
    const expectedInstallationId = deriveInstallationId(normalizedSerial);
    if (installationId && String(installationId) !== expectedInstallationId) {
      throw clientError(400, "installation_mismatch", "Identifiant d’installation incohérent");
    }
    const serialHash = serialFingerprint(normalizedSerial);
    const keyDetails = publicKeyDetails(publicKeyPem);
    const registeredAt = new Date(now()).toISOString();
    const reference = db.ref(`deviceBootstrap/${serialHash}`);
    const result = await reference.transaction(current => ({
      version: 1,
      serialHash,
      installationId: expectedInstallationId,
      publicKey: keyDetails.pem,
      publicKeyFingerprint: keyDetails.fingerprint,
      previousPublicKeyFingerprint: current?.publicKeyFingerprint && current.publicKeyFingerprint !== keyDetails.fingerprint
        ? current.publicKeyFingerprint
        : current?.previousPublicKeyFingerprint || null,
      registeredAt,
      repository: githubRepository,
      workflowRunId: String(claims.run_id),
      workflowSha: String(claims.sha),
      workflowRef: String(claims.workflow_ref || "")
    }));
    if (!result.committed) throw new Error("device_bootstrap_transaction_failed");
    return {
      status: "registered",
      installationId: expectedInstallationId,
      publicKeyFingerprint: keyDetails.fingerprint,
      registeredAt
    };
  }

  async function verifyDeviceRequest(req, action) {
    const normalizedSerial = normalizeSerial(req.body?.serial);
    const serialHash = serialFingerprint(normalizedSerial);
    const timestamp = String(req.headers["x-etr-timestamp"] || "");
    const nonce = String(req.headers["x-etr-nonce"] || "");
    const signature = String(req.headers["x-etr-signature"] || "");
    if (!/^\d{10,13}$/.test(timestamp)) throw clientError(401, "device_signature_missing", "Signature de l’EtR manquante");
    if (!/^[A-Za-z0-9_-]{16,80}$/.test(nonce) || !/^[A-Za-z0-9_-]{80,120}$/.test(signature)) {
      throw clientError(401, "device_signature_invalid", "Signature de l’EtR invalide");
    }
    const timestampSeconds = Number(timestamp);
    const currentSeconds = Math.floor(now() / 1000);
    if (!Number.isSafeInteger(timestampSeconds) || Math.abs(currentSeconds - timestampSeconds) > SIGNATURE_WINDOW_SECONDS) {
      throw clientError(401, "device_signature_expired", "Signature de l’EtR expirée");
    }
    const nonceKey = `${serialHash}:${nonce}`;
    cleanupNonces(now());
    if ((usedNonces.get(nonceKey) || 0) > now()) {
      throw clientError(409, "device_signature_replayed", "Demande d’activation déjà utilisée");
    }

    const record = (await db.ref(`deviceBootstrap/${serialHash}`).get()).val();
    if (!record?.publicKey || record.installationId !== deriveInstallationId(normalizedSerial)) {
      throw clientError(403, "device_not_registered", "Cet EtR n’est pas enregistré par ORYX");
    }
    const keyDetails = publicKeyDetails(record.publicKey);
    if (record.publicKeyFingerprint && record.publicKeyFingerprint !== keyDetails.fingerprint) {
      throw clientError(403, "device_key_mismatch", "Identité de l’EtR incohérente");
    }
    const payload = canonicalRequest({
      action,
      serial: normalizedSerial,
      activationCode: req.body?.activationCode || req.body?.code,
      hostname: req.body?.hostname,
      rotationToken: req.body?.rotationToken,
      timestamp,
      nonce
    });
    let signatureBytes;
    try {
      signatureBytes = Buffer.from(signature, "base64url");
    } catch {
      throw clientError(401, "device_signature_invalid", "Signature de l’EtR invalide");
    }
    if (!crypto.verify(null, payload, keyDetails.key, signatureBytes)) {
      throw clientError(401, "device_signature_invalid", "Signature de l’EtR invalide");
    }
    usedNonces.set(nonceKey, now() + NONCE_TTL_MS);
    return {
      serial: normalizedSerial,
      serialHash,
      installationId: record.installationId,
      publicKeyFingerprint: keyDetails.fingerprint
    };
  }

  return { register, verifyDeviceRequest, verifyWorkflowToken };
}

export function installDeviceBootstrapRoute({ app, service }) {
  app.post("/api/enrollment/bootstrap", async (req, res) => {
    try {
      const result = await service.register({
        token: bearer(req),
        serial: req.body?.serial,
        installationId: req.body?.installationId,
        publicKeyPem: req.body?.publicKey
      });
      res.setHeader("Cache-Control", "no-store");
      return res.status(201).json(result);
    } catch (error) {
      return safeJsonError(res, error);
    }
  });
}

export const DEVICE_BOOTSTRAP_POLICY = Object.freeze({
  algorithm: "Ed25519",
  signatureWindowSeconds: SIGNATURE_WINDOW_SECONDS,
  nonceTtlSeconds: Math.floor(NONCE_TTL_MS / 1000),
  githubAudience: "etr-bootstrap"
});
