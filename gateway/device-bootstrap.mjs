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
const FACTORY_TICKET_MIN_SECONDS = 10 * 60;
const FACTORY_TICKET_MAX_SECONDS = 7 * 24 * 60 * 60;
const FACTORY_TICKET_DEFAULT_SECONDS = 24 * 60 * 60;
const DEFAULT_FACTORY_INSTALLATION = "etr-0000dd7429c2";
const DEFAULT_FACTORY_INSTALLATIONS = `${DEFAULT_FACTORY_INSTALLATION},etr-core`;

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

function normalizeFactoryInstallations(value) {
  const source = Array.isArray(value) ? value : String(value || "").split(",");
  const installations = source
    .map(item => String(item || "").trim().toLowerCase())
    .filter(item => /^etr-[a-z0-9._-]{4,76}$/.test(item));
  if (!installations.length) installations.push(DEFAULT_FACTORY_INSTALLATION);
  return new Set(installations);
}

function factoryTokenHash(token) {
  return crypto.createHash("sha256").update(String(token || ""), "utf8").digest("hex");
}

function factoryTicketToken(value) {
  const token = String(value || "").trim();
  if (!/^[A-Za-z0-9_-]{40,120}$/.test(token)) {
    throw clientError(400, "factory_ticket_invalid", "Ticket de fabrication invalide");
  }
  return token;
}

export function createDeviceBootstrapService({
  db,
  now = () => Date.now(),
  githubRepository = process.env.ETR_GITHUB_REPOSITORY || "ORYX-WORLD/EtR-core",
  githubJwks = GITHUB_JWKS,
  factoryInstallations = process.env.ETR_FACTORY_INSTALLATION_IDS || DEFAULT_FACTORY_INSTALLATIONS,
  randomBytes = crypto.randomBytes
}) {
  if (!db?.ref) throw new Error("Device bootstrap requires Firebase Database");
  const usedNonces = new Map();
  const allowedFactoryInstallations = normalizeFactoryInstallations(factoryInstallations);

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

  function verifyFactoryPrincipal(decodedUser) {
    const installationId = String(decodedUser?.installationId || "").trim().toLowerCase();
    const uid = String(decodedUser?.uid || decodedUser?.sub || "").trim();
    if (decodedUser?.etrDevice !== true || !uid || !allowedFactoryInstallations.has(installationId)) {
      throw clientError(403, "factory_device_refused", "Cet EtR n’est pas autorisé à fabriquer des cartes");
    }
    return { installationId, uid };
  }

  async function issueFactoryTicket({ decodedUser, expiresIn } = {}) {
    const factory = verifyFactoryPrincipal(decodedUser);
    const requestedTtl = Number(expiresIn || FACTORY_TICKET_DEFAULT_SECONDS);
    const ttlSeconds = Math.max(
      FACTORY_TICKET_MIN_SECONDS,
      Math.min(FACTORY_TICKET_MAX_SECONDS, Number.isFinite(requestedTtl) ? requestedTtl : FACTORY_TICKET_DEFAULT_SECONDS)
    );
    const token = randomBytes(32).toString("base64url");
    const tokenHash = factoryTokenHash(token);
    const issuedAtEpoch = now();
    const expiresAtEpoch = issuedAtEpoch + ttlSeconds * 1000;
    const issuedAt = new Date(issuedAtEpoch).toISOString();
    const expiresAt = new Date(expiresAtEpoch).toISOString();
    const record = {
      version: 1,
      status: "issued",
      tokenHash,
      issuedAt,
      issuedAtEpoch,
      expiresAt,
      expiresAtEpoch,
      issuedByUid: factory.uid,
      issuedByInstallationId: factory.installationId
    };
    const result = await db.ref(`factoryBootstrapTickets/${tokenHash}`).transaction(current => current ? undefined : record);
    if (!result.committed) throw new Error("factory_ticket_collision");
    return { status: "issued", ticket: token, issuedAt, expiresAt, expiresIn: ttlSeconds };
  }

  async function redeemFactoryTicket({ ticket, serial, installationId, publicKeyPem, hostname } = {}) {
    const normalizedTicket = factoryTicketToken(ticket);
    const normalizedSerial = normalizeSerial(serial);
    const expectedInstallationId = deriveInstallationId(normalizedSerial);
    if (installationId && String(installationId) !== expectedInstallationId) {
      throw clientError(400, "installation_mismatch", "Identifiant d’installation incohérent");
    }
    const serialHash = serialFingerprint(normalizedSerial);
    const keyDetails = publicKeyDetails(publicKeyPem);
    const tokenHash = factoryTokenHash(normalizedTicket);
    const ticketRef = db.ref(`factoryBootstrapTickets/${tokenHash}`);
    const existingTicket = (await ticketRef.get()).val();
    if (!existingTicket) throw clientError(404, "factory_ticket_unknown", "Ticket de fabrication inconnu");
    if (existingTicket.status === "used") {
      if (existingTicket.serialHash === serialHash && existingTicket.publicKeyFingerprint === keyDetails.fingerprint) {
        return {
          status: "already_registered",
          installationId: expectedInstallationId,
          publicKeyFingerprint: keyDetails.fingerprint,
          registeredAt: existingTicket.usedAt
        };
      }
      throw clientError(409, "factory_ticket_used", "Ticket de fabrication déjà utilisé");
    }
    if (Number(existingTicket.expiresAtEpoch || 0) <= now()) {
      throw clientError(410, "factory_ticket_expired", "Ticket de fabrication expiré");
    }

    const usedAt = new Date(now()).toISOString();
    const reserved = await ticketRef.transaction(current => {
      if (!current || current.status !== "issued" || Number(current.expiresAtEpoch || 0) <= now()) return undefined;
      return {
        ...current,
        status: "used",
        usedAt,
        serialHash,
        installationId: expectedInstallationId,
        publicKeyFingerprint: keyDetails.fingerprint,
        hostname: String(hostname || "").slice(0, 128)
      };
    });
    if (!reserved.committed) {
      const latest = (await ticketRef.get()).val();
      if (latest?.status === "used" && latest.serialHash === serialHash && latest.publicKeyFingerprint === keyDetails.fingerprint) {
        return {
          status: "already_registered",
          installationId: expectedInstallationId,
          publicKeyFingerprint: keyDetails.fingerprint,
          registeredAt: latest.usedAt
        };
      }
      throw clientError(409, "factory_ticket_used", "Ticket de fabrication déjà utilisé");
    }

    const bootstrapRef = db.ref(`deviceBootstrap/${serialHash}`);
    const bootstrap = await bootstrapRef.transaction(current => {
      if (current?.publicKeyFingerprint && current.publicKeyFingerprint !== keyDetails.fingerprint) return undefined;
      return {
        version: 1,
        serialHash,
        installationId: expectedInstallationId,
        publicKey: keyDetails.pem,
        publicKeyFingerprint: keyDetails.fingerprint,
        previousPublicKeyFingerprint: current?.previousPublicKeyFingerprint || null,
        registeredAt: current?.registeredAt || usedAt,
        repository: githubRepository,
        provisioningMode: "factory-ticket",
        factoryTicketHash: tokenHash,
        factoryInstallationId: existingTicket.issuedByInstallationId,
        factoryUid: existingTicket.issuedByUid
      };
    });
    if (!bootstrap.committed) {
      await ticketRef.transaction(current => {
        if (current?.status !== "used" || current.serialHash !== serialHash) return current;
        const { usedAt: _usedAt, serialHash: _serialHash, installationId: _installationId,
          publicKeyFingerprint: _fingerprint, hostname: _hostname, ...rest } = current;
        return { ...rest, status: "issued" };
      });
      throw clientError(409, "device_already_registered", "Cet EtR possède déjà une autre identité");
    }
    return {
      status: "registered",
      installationId: expectedInstallationId,
      publicKeyFingerprint: keyDetails.fingerprint,
      registeredAt: bootstrap.snapshot.val()?.registeredAt || usedAt
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

  return {
    register,
    verifyDeviceRequest,
    verifyWorkflowToken,
    issueFactoryTicket,
    redeemFactoryTicket,
    verifyFactoryPrincipal
  };
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

export const FACTORY_PROVISIONING_POLICY = Object.freeze({
  ticketEntropyBits: 256,
  ticketMinimumSeconds: FACTORY_TICKET_MIN_SECONDS,
  ticketMaximumSeconds: FACTORY_TICKET_MAX_SECONDS,
  defaultFactoryInstallation: DEFAULT_FACTORY_INSTALLATION,
  oneTimeUse: true
});
