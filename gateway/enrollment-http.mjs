import {
  EnrollmentError,
  createEnrollmentService,
  createFirebaseEnrollmentStore
} from "./enrollment.mjs";
import { createDeviceBootstrapService, installDeviceBootstrapRoute } from "./device-bootstrap.mjs";
import { createFirebaseDeviceSessionIssuer } from "./firebase-device-session.mjs";

const WINDOW_MS = 15 * 60 * 1000;

function clientAddress(req) {
  const forwarded = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim();
  return forwarded || req.socket?.remoteAddress || "unknown";
}

function bearer(req) {
  const value = String(req.headers.authorization || "");
  return value.startsWith("Bearer ") ? value.slice(7) : "";
}

function createRateLimiter(now = () => Date.now()) {
  const buckets = new Map();
  return function enforce(req, category, maximum) {
    const key = `${category}:${clientAddress(req)}`;
    const current = buckets.get(key);
    const timestamp = now();
    if (!current || current.resetAt <= timestamp) {
      buckets.set(key, { count: 1, resetAt: timestamp + WINDOW_MS });
      return;
    }
    current.count += 1;
    if (current.count > maximum) throw new EnrollmentError(429, "rate_limited", "Trop de tentatives, réessayez plus tard");
    if (buckets.size > 2000) for (const [bucketKey, bucket] of buckets) if (bucket.resetAt <= timestamp) buckets.delete(bucketKey);
  };
}

function safeError(res, error) {
  if (error instanceof EnrollmentError) return res.status(error.status).json({ error: error.message, code: error.code });
  console.error("Enrollment request failed", {
    name: error?.name || "Error",
    code: error?.code || "",
    message: String(error?.message || error || "unknown").slice(0, 400)
  });
  return res.status(500).json({ error: "Erreur d’activation EtR", code: "enrollment_error" });
}

function enrollmentAuthAdapter(auth, issuer) {
  return {
    getUser: auth.getUser.bind(auth),
    createUser: auth.createUser.bind(auth),
    createCustomToken: (uid, claims) => issuer.issue(uid, claims)
  };
}

function publicExchangeResult(result) {
  const session = result?.customToken;
  if (!session || typeof session !== "object") return result;
  const { customToken: _privateField, ...exchange } = result;
  return {
    ...exchange,
    idToken: session.idToken,
    refreshToken: session.refreshToken,
    expiresIn: session.expiresIn,
    authMode: session.authMode
  };
}

export function installEnrollmentRoutes({
  app,
  db,
  auth,
  verifyIdToken,
  deviceBootstrap = createDeviceBootstrapService({ db, now: () => Date.now() }),
  deviceSessionIssuer = createFirebaseDeviceSessionIssuer({ auth }),
  now = () => Date.now()
}) {
  if (!deviceBootstrap?.verifyDeviceRequest || !deviceBootstrap?.verifyWorkflowToken) throw new Error("Enrollment routes require device bootstrap verification");
  if (!deviceSessionIssuer?.issue || !deviceSessionIssuer?.health) throw new Error("Enrollment routes require Firebase device session issuance");
  const enrollment = createEnrollmentService({
    store: createFirebaseEnrollmentStore(db),
    auth: enrollmentAuthAdapter(auth, deviceSessionIssuer),
    now
  });
  const enforceRate = createRateLimiter(now);
  installDeviceBootstrapRoute({ app, service: deviceBootstrap });

  app.post("/api/enrollment/session-health", async (req, res) => {
    try {
      enforceRate(req, "session-health", 20);
      const claims = await deviceBootstrap.verifyWorkflowToken(bearer(req));
      const result = await deviceSessionIssuer.health(String(claims.run_id));
      res.setHeader("Cache-Control", "no-store");
      return res.json(result);
    } catch (error) {
      return safeError(res, error);
    }
  });

  async function requestEnrollment(req, res) {
    try {
      enforceRate(req, "request", 12);
      await deviceBootstrap.verifyDeviceRequest(req, "request");
      const result = await enrollment.request({ serial: req.body?.serial, hostname: req.body?.hostname, rotationToken: req.body?.rotationToken });
      res.setHeader("Cache-Control", "no-store");
      return res.status(201).json(result);
    } catch (error) {
      return safeError(res, error);
    }
  }

  async function claimEnrollment(req, res) {
    try {
      enforceRate(req, "claim", 30);
      const decodedUser = await verifyIdToken(bearer(req));
      const result = await enrollment.claim({
        serial: req.body?.serial,
        activationCode: req.body?.activationCode || req.body?.code,
        decodedUser
      });
      res.setHeader("Cache-Control", "no-store");
      return res.json(result);
    } catch (error) {
      return safeError(res, error);
    }
  }

  async function exchangeEnrollment(req, res) {
    try {
      enforceRate(req, "exchange", 60);
      await deviceBootstrap.verifyDeviceRequest(req, "exchange");
      const result = await enrollment.exchange({ serial: req.body?.serial, activationCode: req.body?.activationCode || req.body?.code });
      res.setHeader("Cache-Control", "no-store");
      return res.json(publicExchangeResult(result));
    } catch (error) {
      return safeError(res, error);
    }
  }

  app.post("/api/enrollment/request", requestEnrollment);
  app.post("/api/enrollment/claim", claimEnrollment);
  app.post("/api/enrollment/exchange", exchangeEnrollment);
  app.post("/api/enrollment", async (req, res) => {
    const action = String(req.body?.action || "").trim().toLowerCase();
    if (action === "request") return requestEnrollment(req, res);
    if (action === "claim") return claimEnrollment(req, res);
    if (action === "exchange") return exchangeEnrollment(req, res);
    return res.status(400).json({ error: "Action d’activation invalide", code: "invalid_action" });
  });

  return enrollment;
}
