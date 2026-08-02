import {
  ADMIN_POLICY,
  createAdminService as createLegacyAdminService
} from "./admin-legacy.mjs";

export { ADMIN_POLICY };

const ROLES = new Set(ADMIN_POLICY.roles);
const RECENT_AUTH_SECONDS = Number(ADMIN_POLICY.recentAuthenticationSeconds || 15 * 60);
const MAX_USERS = Number(ADMIN_POLICY.maximumUsers || 500);
const MAX_INSTALLATIONS = Number(ADMIN_POLICY.maximumInstallations || 500);
const MAX_AUDIT = Number(ADMIN_POLICY.maximumAuditEntries || 250);

function httpError(status, code, message) {
  return Object.assign(new Error(message), { status, code });
}

function safeText(value, maximum = 500, required = false, label = "champ") {
  const text = String(value ?? "")
    .replace(/[\u0000-\u001f\u007f]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (required && !text) throw httpError(400, "admin/invalid-input", `${label} obligatoire`);
  if (text.length > maximum) throw httpError(400, "admin/invalid-input", `${label} trop long`);
  return text;
}

function safeUid(value) {
  const uid = safeText(value, 128, true, "UID");
  if (!/^[A-Za-z0-9._:@-]{1,128}$/.test(uid)) {
    throw httpError(400, "admin/invalid-uid", "UID invalide");
  }
  return uid;
}

function safeInstallationId(value) {
  const installationId = safeText(value, 80, true, "Installation");
  if (!/^[A-Za-z0-9._-]{2,80}$/.test(installationId)) {
    throw httpError(400, "admin/invalid-installation", "Installation invalide");
  }
  return installationId;
}

function safeReason(value) {
  const reason = safeText(value, 500, true, "Motif");
  if (reason.length < 5) {
    throw httpError(400, "admin/invalid-reason", "Le motif doit contenir au moins 5 caractères");
  }
  return reason;
}

function objectValue(snapshot) {
  const value = snapshot?.val?.();
  return value && typeof value === "object" ? value : {};
}

function isoDate(value) {
  const date = new Date(value || "");
  return Number.isFinite(date.getTime()) ? date.toISOString() : null;
}

function listLimit(value, maximum) {
  const parsed = Number(value || maximum);
  if (!Number.isInteger(parsed)) return maximum;
  return Math.max(1, Math.min(maximum, parsed));
}

function normalizedEmail(value) {
  return String(value || "").trim().toLowerCase();
}

function bootstrapEmailSet(value) {
  return new Set(
    String(value || "")
      .split(",")
      .map(normalizedEmail)
      .filter(Boolean)
  );
}

function claimLevel(decoded) {
  if (decoded?.oryxDeveloper === true || decoded?.oryxAdmin === true) return "write";
  if (decoded?.oryxStaff === true) return "read";
  return "none";
}

function allowlistedAdministrator(decoded, allowlist) {
  return decoded?.email_verified === true && allowlist.has(normalizedEmail(decoded?.email));
}

function effectiveLevel(decoded, allowlist) {
  const claimed = claimLevel(decoded);
  if (claimed !== "none") return claimed;
  return allowlistedAdministrator(decoded, allowlist) ? "write" : "none";
}

function elevatedPrincipal(decoded, allowlist) {
  if (!allowlistedAdministrator(decoded, allowlist)) return decoded;
  if (claimLevel(decoded) !== "none") return decoded;
  return { ...decoded, oryxAdmin: true, oryxStaff: true };
}

function recentAuthentication(decoded, now) {
  const authTime = Number(decoded?.auth_time || 0);
  const current = Math.floor(now() / 1000);
  return Number.isFinite(authTime) && authTime > 0 && current - authTime <= RECENT_AUTH_SECONDS;
}

function authPermissionUnavailable(error) {
  const code = String(error?.code || "").toLowerCase();
  const message = String(error?.message || "").toLowerCase();
  return [
    "auth/insufficient-permission",
    "auth/invalid-credential",
    "app/invalid-credential"
  ].includes(code) || (
    message.includes("insufficient permission") ||
    message.includes("permission denied") ||
    message.includes("insufficient authentication scopes")
  );
}

function auditRecord({ actor, action, targetType, targetId, reason, before = null, after = null, now }) {
  return {
    version: 1,
    at: new Date(now()).toISOString(),
    actorUid: actor.uid,
    actorEmail: actor.email || null,
    action,
    targetType,
    targetId,
    reason,
    before,
    after
  };
}

function directoryMemberships(uid, memberships) {
  const installations = memberships?.[uid] && typeof memberships[uid] === "object"
    ? memberships[uid]
    : {};
  return Object.entries(installations)
    .map(([installationId, membership]) => ({
      installationId,
      role: safeText(membership?.role, 40),
      active: membership?.active === true,
      updatedAt: isoDate(membership?.updatedAt)
    }))
    .sort((a, b) => a.installationId.localeCompare(b.installationId));
}

function databaseUserDirectory({ memberships, adminProfiles, installations, currentPrincipal, allowlist }) {
  const users = new Map();
  const ensure = (uid) => {
    const normalizedUid = safeText(uid, 128);
    if (!normalizedUid) return null;
    if (!users.has(normalizedUid)) {
      users.set(normalizedUid, {
        uid: normalizedUid,
        email: null,
        emailVerified: false,
        disabled: false,
        displayName: null,
        createdAt: null,
        lastSignInAt: null,
        globalRoles: {
          oryxAdmin: false,
          oryxStaff: false,
          oryxDeveloper: false,
          etrDevice: false
        },
        memberships: directoryMemberships(normalizedUid, memberships),
        directorySource: "realtime-database",
        authStateKnown: false
      });
    }
    return users.get(normalizedUid);
  };

  for (const uid of Object.keys(memberships || {})) ensure(uid);

  for (const [uid, profile] of Object.entries(adminProfiles || {})) {
    const entry = ensure(uid);
    if (!entry || !profile || typeof profile !== "object") continue;
    entry.email = safeText(profile.email, 254) || entry.email;
    entry.emailVerified = Boolean(entry.email);
    entry.displayName = safeText(profile.displayName, 160) || entry.displayName;
    entry.globalRoles.oryxAdmin = profile.role === "superadmin" || profile.oryxAdmin === true;
    entry.globalRoles.oryxStaff = entry.globalRoles.oryxAdmin || profile.oryxStaff === true;
    entry.authStateKnown = false;
  }

  for (const record of Object.values(installations || {})) {
    const metadata = record?.metadata && typeof record.metadata === "object" ? record.metadata : {};
    const uid = safeText(metadata.owner_uid || metadata.ownerUid, 128);
    if (!uid) continue;
    const entry = ensure(uid);
    if (!entry) continue;
    entry.email = safeText(metadata.owner_email || metadata.ownerEmail, 254) || entry.email;
    entry.emailVerified = Boolean(entry.email);
  }

  if (currentPrincipal?.uid) {
    const entry = ensure(currentPrincipal.uid);
    if (entry) {
      entry.email = safeText(currentPrincipal.email, 254) || entry.email;
      entry.emailVerified = currentPrincipal.email_verified === true;
      const level = effectiveLevel(currentPrincipal, allowlist);
      entry.globalRoles.oryxAdmin = level === "write";
      entry.globalRoles.oryxStaff = level !== "none";
    }
  }

  return [...users.values()].sort((a, b) => String(a.email || a.uid).localeCompare(String(b.email || b.uid)));
}

export function createAdminService({
  db,
  auth,
  adminEmails = process.env.ETR_ADMIN_EMAILS || "amotard.oryx@gmail.com",
  getConnectedInstallations = () => [],
  now = () => Date.now()
}) {
  if (!db?.ref) throw new Error("EtR admin service requires Firebase Realtime Database");
  if (!auth?.getUser || !auth?.listUsers || !auth?.setCustomUserClaims) {
    throw new Error("EtR admin service requires a Firebase Auth adapter");
  }

  const allowlist = bootstrapEmailSet(adminEmails);
  if (!allowlist.size) throw new Error("ETR_ADMIN_EMAILS must contain at least one verified address");
  const legacy = createLegacyAdminService({ db, auth, adminEmails, getConnectedInstallations, now });
  let authAdminAvailableCache = null;

  async function writeAudit(record) {
    await db.ref("adminAudit").push(record);
  }

  async function authAdminAvailable(uid) {
    if (authAdminAvailableCache !== null) return authAdminAvailableCache;
    try {
      await auth.getUser(uid);
      authAdminAvailableCache = true;
    } catch (error) {
      if (!authPermissionUnavailable(error)) throw error;
      authAdminAvailableCache = false;
    }
    return authAdminAvailableCache;
  }

  function capabilitySnapshot(firebaseAuthAdmin) {
    return {
      firebaseAuthAdmin,
      userDirectory: firebaseAuthAdmin ? "firebase-auth" : "realtime-database-index",
      manageMemberships: true,
      manageInstallationMetadata: true,
      transferOwner: firebaseAuthAdmin,
      manageUserStatus: firebaseAuthAdmin,
      revokeSessions: firebaseAuthAdmin
    };
  }

  async function recordAllowlistedProfile(decoded) {
    const path = `adminProfiles/${decoded.uid}`;
    const before = (await db.ref(path).get()).val() || null;
    const email = normalizedEmail(decoded.email);
    const after = {
      ...(before && typeof before === "object" ? before : {}),
      email,
      role: "superadmin",
      active: true,
      grantedAt: before?.grantedAt || new Date(now()).toISOString(),
      grantedBy: before?.grantedBy || "verified-email-allowlist-runtime",
      lastSeenAt: new Date(now()).toISOString()
    };
    await db.ref(path).set(after);
    if (!before || before.active !== true || normalizedEmail(before.email) !== email) {
      await writeAudit(auditRecord({
        actor: decoded,
        action: "admin.allowlist.activate",
        targetType: "user",
        targetId: decoded.uid,
        reason: "Adresse administrateur ORYX vérifiée dans la liste Cloud Run",
        before,
        after: { email, role: "superadmin", active: true },
        now
      }));
    }
  }

  async function ensureSession(decoded) {
    const level = effectiveLevel(decoded, allowlist);
    if (level === "none") {
      throw httpError(403, "admin/access-denied", "Compte non autorisé pour l’administration EtR");
    }

    const firebaseAuthAdmin = await authAdminAvailable(decoded.uid);
    if (firebaseAuthAdmin) {
      try {
        const session = await legacy.ensureSession(decoded);
        return {
          ...session,
          authorizationSource: claimLevel(decoded) !== "none" ? "firebase-claims" : "verified-email-allowlist",
          capabilities: capabilitySnapshot(true)
        };
      } catch (error) {
        if (!authPermissionUnavailable(error)) throw error;
        authAdminAvailableCache = false;
      }
    }

    if (allowlistedAdministrator(decoded, allowlist)) await recordAllowlistedProfile(decoded);
    return {
      authorized: true,
      level,
      refreshRequired: false,
      uid: decoded.uid,
      email: decoded.email || null,
      recentAuthentication: recentAuthentication(decoded, now),
      authorizationSource: claimLevel(decoded) !== "none" ? "firebase-claims" : "verified-email-allowlist",
      capabilities: capabilitySnapshot(false)
    };
  }

  function elevated(decoded) {
    const level = effectiveLevel(decoded, allowlist);
    if (level === "none") {
      throw httpError(403, "admin/access-denied", "Accès administrateur requis");
    }
    return elevatedPrincipal(decoded, allowlist);
  }

  function requireRecentWrite(decoded) {
    if (effectiveLevel(decoded, allowlist) !== "write") {
      throw httpError(403, "admin/write-denied", "Droit administrateur en écriture requis");
    }
    if (!recentAuthentication(decoded, now)) {
      throw httpError(401, "admin/recent-auth-required", "Reconnectez-vous avant cette opération sensible");
    }
  }

  async function fallbackOverview(decoded) {
    const connected = new Set(getConnectedInstallations());
    const [installationsSnap, membershipsSnap, enrollmentSnap, adminProfilesSnap] = await Promise.all([
      db.ref("installations").get(),
      db.ref("memberships").get(),
      db.ref("enrollmentRequests").get(),
      db.ref("adminProfiles").get()
    ]);
    const installations = objectValue(installationsSnap);
    const memberships = objectValue(membershipsSnap);
    const enrollments = objectValue(enrollmentSnap);
    const adminProfiles = objectValue(adminProfilesSnap);
    const users = databaseUserDirectory({ memberships, adminProfiles, installations, currentPrincipal: decoded, allowlist });
    const activeMemberships = Object.values(memberships).reduce((total, list) => {
      if (!list || typeof list !== "object") return total;
      return total + Object.values(list).filter((membership) => membership?.active === true).length;
    }, 0);
    return {
      generatedAt: new Date(now()).toISOString(),
      installations: {
        total: Object.keys(installations).length,
        connected: Object.keys(installations).filter((id) => connected.has(id)).length
      },
      users: {
        total: users.length,
        disabled: null,
        truncated: false,
        source: "realtime-database-index",
        authManagementAvailable: false
      },
      memberships: { active: activeMemberships },
      enrollments: {
        pending: Object.values(enrollments).filter((record) => ["pending", "claimed"].includes(record?.status)).length,
        completed: Object.values(enrollments).filter((record) => record?.status === "completed").length
      }
    };
  }

  async function overview(decoded) {
    const principal = elevated(decoded);
    if (await authAdminAvailable(decoded.uid)) {
      try {
        const result = await legacy.overview(principal);
        return {
          ...result,
          users: { ...result.users, source: "firebase-auth", authManagementAvailable: true }
        };
      } catch (error) {
        if (!authPermissionUnavailable(error)) throw error;
        authAdminAvailableCache = false;
      }
    }
    return fallbackOverview(decoded);
  }

  async function listInstallations(decoded, limit = MAX_INSTALLATIONS) {
    return legacy.listInstallations(elevated(decoded), limit);
  }

  async function fallbackUsers(decoded, limit) {
    const [membershipsSnap, adminProfilesSnap, installationsSnap] = await Promise.all([
      db.ref("memberships").get(),
      db.ref("adminProfiles").get(),
      db.ref("installations").get()
    ]);
    const items = databaseUserDirectory({
      memberships: objectValue(membershipsSnap),
      adminProfiles: objectValue(adminProfilesSnap),
      installations: objectValue(installationsSnap),
      currentPrincipal: decoded,
      allowlist
    });
    const maximum = listLimit(limit, MAX_USERS);
    return {
      items: items.slice(0, maximum),
      total: items.length,
      truncated: items.length > maximum,
      source: "realtime-database-index",
      authManagementAvailable: false
    };
  }

  async function listUsers(decoded, limit = MAX_USERS) {
    const principal = elevated(decoded);
    if (await authAdminAvailable(decoded.uid)) {
      try {
        const result = await legacy.listUsers(principal, limit);
        return { ...result, source: "firebase-auth", authManagementAvailable: true };
      } catch (error) {
        if (!authPermissionUnavailable(error)) throw error;
        authAdminAvailableCache = false;
      }
    }
    return fallbackUsers(decoded, limit);
  }

  async function listEnrollments(decoded, limit = 200) {
    return legacy.listEnrollments(elevated(decoded), limit);
  }

  async function listAudit(decoded, limit = MAX_AUDIT) {
    return legacy.listAudit(elevated(decoded), limit);
  }

  async function fallbackSetMembership(decoded, input) {
    requireRecentWrite(decoded);
    const uid = safeUid(input?.uid);
    const installationId = safeInstallationId(input?.installationId);
    const role = safeText(input?.role, 40, true, "Rôle");
    const active = input?.active === true;
    const reason = safeReason(input?.reason);
    if (!ROLES.has(role)) throw httpError(400, "admin/invalid-role", "Rôle EtR invalide");
    const installation = (await db.ref(`installations/${installationId}`).get()).val();
    if (!installation) throw httpError(404, "admin/installation-not-found", "Installation EtR introuvable");

    const path = `memberships/${uid}/${installationId}`;
    const before = (await db.ref(path).get()).val() || null;
    const after = {
      role,
      active,
      updatedAt: new Date(now()).toISOString(),
      updatedBy: decoded.uid
    };
    await db.ref().update({
      [path]: after,
      [`userInstallations/${uid}/${installationId}`]: after
    });
    await writeAudit(auditRecord({
      actor: decoded,
      action: "membership.set",
      targetType: "membership",
      targetId: `${uid}:${installationId}`,
      reason,
      before,
      after: { ...after, targetEmail: null, directoryValidation: "uid-format-only" },
      now
    }));
    return { ok: true, membership: after, authManagementAvailable: false };
  }

  async function setMembership(decoded, input) {
    const principal = elevated(decoded);
    if (await authAdminAvailable(decoded.uid)) {
      try {
        return await legacy.setMembership(principal, input);
      } catch (error) {
        if (!authPermissionUnavailable(error)) throw error;
        authAdminAvailableCache = false;
      }
    }
    return fallbackSetMembership(decoded, input);
  }

  async function updateInstallationMetadata(decoded, input) {
    return legacy.updateInstallationMetadata(elevated(decoded), input);
  }

  function firebaseAuthManagementRequired() {
    throw httpError(
      409,
      "admin/firebase-auth-management-unavailable",
      "La gestion de sécurité des comptes Firebase est temporairement indisponible. Les installations, droits EtR, enrôlements et écrans restent administrables."
    );
  }

  async function transferOwner(decoded, input) {
    if (!(await authAdminAvailable(decoded.uid))) return firebaseAuthManagementRequired();
    try {
      return await legacy.transferOwner(elevated(decoded), input);
    } catch (error) {
      if (!authPermissionUnavailable(error)) throw error;
      authAdminAvailableCache = false;
      return firebaseAuthManagementRequired();
    }
  }

  async function setUserStatus(decoded, input) {
    if (!(await authAdminAvailable(decoded.uid))) return firebaseAuthManagementRequired();
    try {
      return await legacy.setUserStatus(elevated(decoded), input);
    } catch (error) {
      if (!authPermissionUnavailable(error)) throw error;
      authAdminAvailableCache = false;
      return firebaseAuthManagementRequired();
    }
  }

  async function revokeSessions(decoded, input) {
    if (!(await authAdminAvailable(decoded.uid))) return firebaseAuthManagementRequired();
    try {
      return await legacy.revokeSessions(elevated(decoded), input);
    } catch (error) {
      if (!authPermissionUnavailable(error)) throw error;
      authAdminAvailableCache = false;
      return firebaseAuthManagementRequired();
    }
  }

  return {
    ensureSession,
    overview,
    listInstallations,
    listUsers,
    listEnrollments,
    listAudit,
    setMembership,
    updateInstallationMetadata,
    transferOwner,
    setUserStatus,
    revokeSessions
  };
}

function bearer(req) {
  const value = String(req.headers.authorization || "");
  return value.startsWith("Bearer ") ? value.slice(7) : "";
}

function sendError(res, error) {
  const status = Number(error?.status || 500);
  const code = status >= 500 ? "admin/internal-error" : error?.code || "admin/request-failed";
  const message = status >= 500 ? "Erreur d’administration EtR" : error?.message || "Requête refusée";
  if (status >= 500) {
    console.error("EtR admin request failed", {
      name: error?.name || "Error",
      code: error?.code || "",
      message: String(error?.message || error || "unknown").slice(0, 500)
    });
  }
  return res.status(status).json({ error: message, code });
}

export function installAdminRoutes({
  app,
  db,
  auth,
  verifyIdToken,
  getConnectedInstallations = () => [],
  adminEmails = process.env.ETR_ADMIN_EMAILS || "amotard.oryx@gmail.com",
  now = () => Date.now()
}) {
  const service = createAdminService({ db, auth, adminEmails, getConnectedInstallations, now });

  async function decoded(req) {
    return verifyIdToken(bearer(req));
  }

  const routes = [
    ["get", "/api/admin/session", "ensureSession"],
    ["get", "/api/admin/overview", "overview"],
    ["get", "/api/admin/installations", "listInstallations", "limit"],
    ["get", "/api/admin/users", "listUsers", "limit"],
    ["get", "/api/admin/enrollments", "listEnrollments", "limit"],
    ["get", "/api/admin/audit", "listAudit", "limit"]
  ];

  for (const [verb, path, method, queryName] of routes) {
    app[verb](path, async (req, res) => {
      try {
        res.setHeader("Cache-Control", "no-store");
        const principal = await decoded(req);
        const args = queryName ? [principal, req.query?.[queryName]] : [principal];
        return res.json(await service[method](...args));
      } catch (error) {
        return sendError(res, error);
      }
    });
  }

  const actions = [
    ["/api/admin/membership", "setMembership"],
    ["/api/admin/installation-metadata", "updateInstallationMetadata"],
    ["/api/admin/transfer-owner", "transferOwner"],
    ["/api/admin/user-status", "setUserStatus"],
    ["/api/admin/revoke-sessions", "revokeSessions"]
  ];
  for (const [path, method] of actions) {
    app.post(path, async (req, res) => {
      try {
        res.setHeader("Cache-Control", "no-store");
        return res.json(await service[method](await decoded(req), req.body || {}));
      } catch (error) {
        return sendError(res, error);
      }
    });
  }

  return service;
}
