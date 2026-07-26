import crypto from "node:crypto";

// Crockford Base32: 32 symbols, no I/L/O/U. Twenty characters encode exactly
// 100 bits and remain readable on a small commissioning display.
const ACTIVATION_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";
const ACTIVATION_LENGTH = 20;
const REQUEST_TTL_MS = 24 * 60 * 60 * 1000;
const MAX_ATTEMPTS = 8;

export class EnrollmentError extends Error {
  constructor(status, code, message) {
    super(message);
    this.name = "EnrollmentError";
    this.status = status;
    this.code = code;
  }
}

export function normalizeSerial(value) {
  const serial = String(value || "").replace(/[^A-Za-z0-9]/g, "").toUpperCase();
  if (serial.length < 8 || serial.length > 64) {
    throw new EnrollmentError(400, "invalid_serial", "Numéro de série invalide");
  }
  return serial;
}

export function normalizeActivationCode(value) {
  const code = String(value || "")
    .replace(/[^A-Za-z0-9]/g, "")
    .toUpperCase()
    .replaceAll("O", "0")
    .replace(/[IL]/g, "1");
  if (code.length !== ACTIVATION_LENGTH || [...code].some(character => !ACTIVATION_ALPHABET.includes(character))) {
    throw new EnrollmentError(400, "invalid_activation_code", "Code d’activation invalide");
  }
  return code;
}

export function deriveInstallationId(serial) {
  return `etr-${normalizeSerial(serial).slice(-12).toLowerCase()}`;
}

export function serialFingerprint(serial) {
  return crypto.createHash("sha256").update(normalizeSerial(serial), "ascii").digest("hex");
}

export function deriveDeviceUid(serialHash) {
  if (!/^[a-f0-9]{64}$/.test(String(serialHash || ""))) throw new Error("invalid_serial_hash");
  return `etrdev_${serialHash.slice(0, 48)}`;
}

export function formatActivationCode(code) {
  const normalized = normalizeActivationCode(code);
  return normalized.match(/.{1,5}/g).join("-");
}

export function generateActivationCode(randomBytes = crypto.randomBytes) {
  const bytes = randomBytes(ACTIVATION_LENGTH);
  let result = "";
  for (let index = 0; index < ACTIVATION_LENGTH; index += 1) {
    result += ACTIVATION_ALPHABET[bytes[index] % ACTIVATION_ALPHABET.length];
  }
  return result;
}

function secretHash(kind, serialHash, secret) {
  return crypto.createHash("sha256").update(`${kind}:${serialHash}:${secret}`, "utf8").digest("hex");
}

function constantTimeEqual(left, right) {
  if (!/^[a-f0-9]{64}$/.test(String(left || "")) || !/^[a-f0-9]{64}$/.test(String(right || ""))) return false;
  return crypto.timingSafeEqual(Buffer.from(left, "hex"), Buffer.from(right, "hex"));
}

function cleanHostname(value) {
  return String(value || "")
    .trim()
    .replace(/[^A-Za-z0-9._-]/g, "-")
    .replace(/-+/g, "-")
    .slice(0, 64);
}

function nowIso(now) {
  return new Date(now()).toISOString();
}

function safeRequestStatus(request, now) {
  if (!request) throw new EnrollmentError(404, "request_not_found", "Demande d’activation introuvable");
  if (Number(request.expiresAt || 0) <= now()) throw new EnrollmentError(410, "request_expired", "Code d’activation expiré");
  if (Number(request.attempts || 0) >= MAX_ATTEMPTS) {
    throw new EnrollmentError(429, "too_many_attempts", "Trop de tentatives d’activation");
  }
}

function verifiedUser(decoded) {
  const uid = String(decoded?.uid || decoded?.sub || "").trim();
  const emailVerified = decoded?.email_verified === true || decoded?.emailVerified === true;
  if (!uid) throw new EnrollmentError(401, "authentication_required", "Authentification requise");
  if (!emailVerified) throw new EnrollmentError(403, "email_not_verified", "L’adresse e-mail doit être vérifiée");
  return { uid, email: String(decoded?.email || "").trim().toLowerCase() };
}

export function createFirebaseEnrollmentStore(db) {
  const requestRef = serialHash => db.ref(`enrollmentRequests/${serialHash}`);

  return {
    async getRequest(serialHash) {
      return (await requestRef(serialHash).get()).val();
    },

    async putRequest(serialHash, request) {
      await requestRef(serialHash).set(request);
    },

    async incrementAttempts(serialHash, timestamp) {
      await requestRef(serialHash).transaction(current => {
        if (!current) return current;
        return {
          ...current,
          attempts: Math.min(MAX_ATTEMPTS, Number(current.attempts || 0) + 1),
          lastAttemptAt: timestamp
        };
      });
    },

    async getOwner(installationId) {
      return (await db.ref(`installations/${installationId}/metadata/owner_uid`).get()).val();
    },

    async claimRequest(serialHash, codeHash, ownerUid, ownerEmail, timestamp) {
      const result = await requestRef(serialHash).transaction(current => {
        if (!current || current.codeHash !== codeHash) return;
        if (current.status === "claimed" && current.ownerUid === ownerUid) return current;
        if (current.status !== "pending") return;
        return {
          ...current,
          status: "claimed",
          ownerUid,
          ownerEmail,
          claimedAt: timestamp
        };
      });
      return result.committed ? result.snapshot.val() : null;
    },

    async applyClaim(installationId, ownerUid, ownerEmail, timestamp) {
      const label = `EtR ${installationId.slice(-12).toUpperCase()}`;
      await db.ref().update({
        [`memberships/${ownerUid}/${installationId}`]: {
          active: true,
          role: "owner",
          label,
          grantedAt: timestamp
        },
        [`userInstallations/${ownerUid}/${installationId}`]: true,
        [`installations/${installationId}/metadata/installation_id`]: installationId,
        [`installations/${installationId}/metadata/label`]: label,
        [`installations/${installationId}/metadata/owner_uid`]: ownerUid,
        [`installations/${installationId}/metadata/owner_email`]: ownerEmail || null,
        [`installations/${installationId}/metadata/enrolled_at`]: timestamp
      });
    },

    async lockExchange(serialHash, codeHash, lockId, timestamp) {
      const result = await requestRef(serialHash).transaction(current => {
        if (!current || current.codeHash !== codeHash || current.status !== "claimed" || !current.ownerUid) return;
        return {
          ...current,
          status: "exchanging",
          exchangeLock: lockId,
          exchangeStartedAt: timestamp
        };
      });
      return result.committed ? result.snapshot.val() : null;
    },

    async bindDevice(installationId, deviceUid, serialHash, timestamp) {
      await db.ref().update({
        [`deviceAccess/${deviceUid}`]: installationId,
        [`installations/${installationId}/metadata/device_uid`]: deviceUid,
        [`installations/${installationId}/metadata/device_fingerprint`]: serialHash,
        [`installations/${installationId}/metadata/device_bound_at`]: timestamp
      });
    },

    async completeExchange(serialHash, lockId, deviceUid, timestamp) {
      const result = await requestRef(serialHash).transaction(current => {
        if (!current || current.status !== "exchanging" || current.exchangeLock !== lockId) return;
        return {
          ...current,
          status: "exchanged",
          deviceUid,
          completedAt: timestamp,
          codeHash: null,
          rotationTokenHash: null,
          exchangeLock: null
        };
      });
      return result.committed;
    },

    async rollbackExchange(serialHash, lockId, timestamp) {
      await requestRef(serialHash).transaction(current => {
        if (!current || current.status !== "exchanging" || current.exchangeLock !== lockId) return current;
        return {
          ...current,
          status: "claimed",
          exchangeLock: null,
          lastExchangeFailureAt: timestamp
        };
      });
    }
  };
}

export function createEnrollmentService({
  store,
  auth = null,
  issueDeviceSession = null,
  now = () => Date.now(),
  randomBytes = crypto.randomBytes
}) {
  if (!store || (!issueDeviceSession && !auth)) {
    throw new Error("Enrollment service requires a store and a device identity issuer");
  }

  async function readAndVerifyCode(serial, activationCode) {
    const normalizedSerial = normalizeSerial(serial);
    const serialHash = serialFingerprint(normalizedSerial);
    const code = normalizeActivationCode(activationCode);
    const request = await store.getRequest(serialHash);
    safeRequestStatus(request, now);
    const providedHash = secretHash("activation", serialHash, code);
    if (!constantTimeEqual(request.codeHash, providedHash)) {
      await store.incrementAttempts(serialHash, nowIso(now));
      throw new EnrollmentError(401, "activation_refused", "Activation refusée");
    }
    return { normalizedSerial, serialHash, codeHash: providedHash, request };
  }

  return {
    async request({ serial, hostname, rotationToken } = {}) {
      const normalizedSerial = normalizeSerial(serial);
      const serialHash = serialFingerprint(normalizedSerial);
      const installationId = deriveInstallationId(normalizedSerial);
      const owner = await store.getOwner(installationId);
      if (owner) throw new EnrollmentError(409, "already_enrolled", "Cet EtR est déjà associé");

      const existing = await store.getRequest(serialHash);
      if (existing && Number(existing.expiresAt || 0) > now()) {
        if (existing.status !== "pending") {
          throw new EnrollmentError(409, "request_in_progress", "L’activation de cet EtR est déjà en cours");
        }
        const suppliedRotationHash = secretHash("rotation", serialHash, String(rotationToken || ""));
        if (!constantTimeEqual(existing.rotationTokenHash, suppliedRotationHash)) {
          throw new EnrollmentError(409, "request_exists", "Une demande d’activation est déjà active");
        }
      }

      const code = generateActivationCode(randomBytes);
      const nextRotationToken = randomBytes(32).toString("base64url");
      const createdAt = now();
      const record = {
        version: 1,
        serialHash,
        installationId,
        hostname: cleanHostname(hostname),
        status: "pending",
        attempts: 0,
        codeHash: secretHash("activation", serialHash, code),
        rotationTokenHash: secretHash("rotation", serialHash, nextRotationToken),
        createdAt,
        expiresAt: createdAt + REQUEST_TTL_MS
      };
      await store.putRequest(serialHash, record);
      return {
        installationId,
        activationCode: formatActivationCode(code),
        rotationToken: nextRotationToken,
        expiresAt: new Date(record.expiresAt).toISOString(),
        expiresIn: Math.floor(REQUEST_TTL_MS / 1000)
      };
    },

    async claim({ serial, activationCode, decodedUser } = {}) {
      const user = verifiedUser(decodedUser);
      const verified = await readAndVerifyCode(serial, activationCode);
      if (!["pending", "claimed"].includes(verified.request.status)) {
        throw new EnrollmentError(409, "request_not_claimable", "Cette demande ne peut plus être associée");
      }
      if (verified.request.status === "claimed" && verified.request.ownerUid !== user.uid) {
        throw new EnrollmentError(409, "already_claimed", "Cet EtR est déjà associé à un autre compte");
      }
      const currentOwner = await store.getOwner(verified.request.installationId);
      if (currentOwner && currentOwner !== user.uid) {
        throw new EnrollmentError(409, "already_owned", "Cet EtR possède déjà un propriétaire");
      }
      const timestamp = nowIso(now);
      const claimed = await store.claimRequest(
        verified.serialHash,
        verified.codeHash,
        user.uid,
        user.email,
        timestamp
      );
      if (!claimed) throw new EnrollmentError(409, "claim_conflict", "L’association a changé, recommencez");
      await store.applyClaim(claimed.installationId, user.uid, user.email, timestamp);
      return {
        installationId: claimed.installationId,
        role: "owner",
        status: "claimed"
      };
    },

    async exchange({ serial, activationCode } = {}) {
      const verified = await readAndVerifyCode(serial, activationCode);
      if (verified.request.status === "pending") {
        throw new EnrollmentError(409, "awaiting_claim", "En attente de l’association par le client");
      }
      if (verified.request.status !== "claimed") {
        throw new EnrollmentError(409, "exchange_unavailable", "L’identité technique n’est pas disponible");
      }

      const lockId = randomBytes(18).toString("base64url");
      const startedAt = nowIso(now);
      const locked = await store.lockExchange(verified.serialHash, verified.codeHash, lockId, startedAt);
      if (!locked) throw new EnrollmentError(409, "exchange_conflict", "Un échange est déjà en cours");

      const deviceUid = deriveDeviceUid(verified.serialHash);
      let completed = false;
      try {
        let identity;
        if (issueDeviceSession) {
          identity = await issueDeviceSession({ deviceUid, installationId: locked.installationId });
        } else {
          try {
            await auth.getUser(deviceUid);
          } catch (error) {
            if (error?.code !== "auth/user-not-found") throw error;
            await auth.createUser({
              uid: deviceUid,
              disabled: false,
              displayName: `EtR ${locked.installationId.slice(-12).toUpperCase()}`
            });
          }
          identity = {
            customToken: await auth.createCustomToken(deviceUid, {
              etrDevice: true,
              installationId: locked.installationId
            }),
            expiresIn: 3600,
            authenticationMethod: "firebase_custom_token"
          };
        }

        await store.bindDevice(locked.installationId, deviceUid, verified.serialHash, startedAt);
        completed = await store.completeExchange(verified.serialHash, lockId, deviceUid, nowIso(now));
        if (!completed) throw new Error("enrollment_completion_conflict");
        return {
          ...identity,
          installationId: locked.installationId,
          deviceUid,
          status: "exchanged"
        };
      } catch (error) {
        if (!completed) await store.rollbackExchange(verified.serialHash, lockId, nowIso(now));
        throw error;
      }
    }
  };
}

export const ENROLLMENT_POLICY = Object.freeze({
  activationBits: 100,
  activationLength: ACTIVATION_LENGTH,
  requestTtlSeconds: Math.floor(REQUEST_TTL_MS / 1000),
  maxAttempts: MAX_ATTEMPTS
});
