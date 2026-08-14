const RECENT_AUTH_SECONDS = 15 * 60;

function bearer(req) {
  const value = String(req.headers.authorization || "");
  return value.startsWith("Bearer ") ? value.slice(7) : "";
}

function normalizedEmail(value) {
  return String(value || "").trim().toLowerCase();
}

function recentAuthentication(decoded, now) {
  const authTime = Number(decoded?.auth_time || 0);
  const current = Math.floor(now() / 1000);
  return Number.isFinite(authTime) && authTime > 0 && current - authTime <= RECENT_AUTH_SECONDS;
}

function canGenerate(decoded) {
  return decoded?.oryxAdmin === true || decoded?.oryxDeveloper === true || decoded?.oryxStaff === true;
}

function safeError(res, error, genericMessage = "Opération de vérification indisponible.") {
  const status = Number(error?.status || 500);
  const message = status >= 500 ? genericMessage : String(error?.message || "Requête refusée");
  if (status >= 500) {
    console.error("EtR email verification operation failed", {
      code: String(error?.code || ""),
      message: String(error?.message || error || "unknown").slice(0, 400)
    });
  }
  res.status(status).json({ error: message, code: error?.code || "admin/email-verification-failed" });
}

async function requireOwnAdmin({ req, auth, verifyIdToken }) {
  const decoded = await verifyIdToken(bearer(req));
  if (!decoded?.uid) {
    throw Object.assign(new Error("Connexion requise."), { status: 401, code: "auth/id-token-missing" });
  }
  if (!canGenerate(decoded)) {
    throw Object.assign(new Error("Droit administrateur ORYX requis."), { status: 403, code: "admin/write-denied" });
  }

  const user = await auth.getUser(decoded.uid);
  const tokenEmail = normalizedEmail(decoded.email);
  const accountEmail = normalizedEmail(user?.email);
  if (!tokenEmail || !accountEmail || tokenEmail !== accountEmail) {
    throw Object.assign(new Error("L’adresse du compte connecté ne correspond pas au compte Firebase."), { status: 409, code: "admin/email-mismatch" });
  }
  return { decoded, user, accountEmail };
}

async function requireOwnRecentAdmin({ req, auth, verifyIdToken, now }) {
  const context = await requireOwnAdmin({ req, auth, verifyIdToken });
  if (!recentAuthentication(context.decoded, now)) {
    throw Object.assign(new Error("Reconnectez-vous avant de poursuivre."), { status: 401, code: "admin/recent-auth-required" });
  }
  return context;
}

export function installAdminVerificationLinkRoute({
  app,
  auth,
  verifyIdToken,
  now = () => Date.now(),
  continueUrl = "https://oryx-froid-industriel.web.app/etr-project/#etr-installations"
}) {
  if (!app?.post) throw new Error("Express app required");
  if (!auth?.getUser || !auth?.generateEmailVerificationLink || !auth?.updateUser) {
    throw new Error("Firebase Auth Admin email verification support required");
  }

  app.post("/api/admin/self-email-verification-link", async (req, res) => {
    res.setHeader("Cache-Control", "no-store");
    try {
      // Generating a link is self-only and read-like: a valid ORYX session is enough.
      // Do not require auth_time here because fallback token verification may omit it.
      const { decoded, user, accountEmail } = await requireOwnAdmin({ req, auth, verifyIdToken });
      if (user.emailVerified === true) {
        return res.json({ ok: true, alreadyVerified: true, email: accountEmail });
      }

      const verificationUrl = await auth.generateEmailVerificationLink(accountEmail, {
        url: continueUrl,
        handleCodeInApp: false
      });
      console.info("EtR self verification link generated", { uid: decoded.uid, email: accountEmail });
      return res.json({
        ok: true,
        alreadyVerified: false,
        email: accountEmail,
        verificationUrl
      });
    } catch (error) {
      return safeError(res, error, "Impossible de générer le lien de vérification.");
    }
  });

  app.post("/api/admin/self-email-verify", async (req, res) => {
    res.setHeader("Cache-Control", "no-store");
    try {
      const { decoded, user, accountEmail } = await requireOwnRecentAdmin({ req, auth, verifyIdToken, now });
      if (user.emailVerified === true) {
        return res.json({ ok: true, alreadyVerified: true, verified: true, email: accountEmail });
      }

      const updated = await auth.updateUser(decoded.uid, { emailVerified: true });
      if (updated?.emailVerified !== true) {
        throw Object.assign(new Error("Firebase n’a pas confirmé la mise à jour du compte."), {
          status: 502,
          code: "admin/email-verification-not-applied"
        });
      }
      console.info("EtR admin self email verified", { uid: decoded.uid, email: accountEmail });
      return res.json({
        ok: true,
        alreadyVerified: false,
        verified: true,
        email: accountEmail,
        mode: "admin-self-verification"
      });
    } catch (error) {
      return safeError(res, error, "Impossible de valider l’adresse e-mail depuis l’administration.");
    }
  });
}

export const ADMIN_VERIFICATION_LINK_POLICY = Object.freeze({
  selfOnly: true,
  globalAdminClaimRequired: true,
  verificationLinkRecentAuthenticationRequired: false,
  directVerificationRecentAuthenticationSeconds: RECENT_AUTH_SECONDS,
  emailOwnershipDeliveryBypass: true,
  directAdminSelfVerification: true
});
