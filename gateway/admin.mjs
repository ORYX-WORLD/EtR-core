import crypto from "node:crypto";

const ROLES = new Set(["owner", "administrator", "installer", "maintenance", "operator", "viewer"]);
const RECENT_AUTH_SECONDS = 15 * 60;
const MAX_USERS = 500;
const MAX_INSTALLATIONS = 500;
const MAX_AUDIT = 250;

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
  if (!/^[A-Za-z0-9._:@-]{1,128}$/.test(uid)) throw httpError(400, "admin/invalid-uid", "UID invalide");
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
  if (reason.length < 5) throw httpError(400, "admin/invalid-reason", "Le motif doit contenir au moins 5 caractères");
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

function adminLevel(decoded) {
  if (decoded?.oryxDeveloper === true || decoded?.oryxAdmin === true) return "write";
  if (decoded?.oryxStaff === true) return "read";
  return "none";
}

function recentAuthentication(decoded, now) {
  const authTime = Number(decoded?.auth_time || 0);
  const current = Math.floor(now() / 1000);
  return Number.isFinite(authTime) && authTime > 0 && current - authTime <= RECENT_AUTH_SECONDS;
}

function listLimit(value, maximum) {
  const parsed = Number(value || maximum);
  if (!Number.isInteger(parsed)) return maximum;
  return Math.max(1, Math.min(maximum, parsed));
}

function summarizeLatest(latest, connected) {
  const value = latest && typeof latest === "object" ? latest : {};
  const measurements = value.measurements && typeof value.measurements === "object" ? value.measurements : {};
  const states = value.states && typeof value.states === "object" ? value.states : {};
  const alerts = Array.isArray(value.alerts) ? value.alerts : [];
  return {
    updatedAt: isoDate(value.updated_at || value.updatedAt),
    bridgeOnline: value.bridge_online === true,
    localApiOnline: value.local_api_online === true,
    hostname: safeText(value.hostname, 120),
    gatewayVersion: safeText(value.gateway_version, 40),
    schemaVersion: safeText(value.schema_version, 40),
    health: safeText(value.health, 40),
    connected,
    measurementCount: Object.keys(measurements).length,
    stateCount: Object.keys(states).length,
    alertCount: alerts.length
  };
}

function sanitizeMetadata(metadata) {
  const value = metadata && typeof metadata === "object" ? metadata : {};
  return {
    displayName: safeText(value.display_name || value.displayName, 160),
    client: safeText(value.client, 160),
    site: safeText(value.site, 160),
    ownerUid: safeText(value.owner_uid || value.ownerUid, 128),
    ownerEmail: safeText(value.owner_email || value.ownerEmail, 254),
    deviceUid: safeText(value.device_uid || value.deviceUid, 128),
    deviceFingerprint: safeText(value.device_fingerprint || value.deviceFingerprint, 128),
    enrolledAt: isoDate(value.enrolled_at || value.enrolledAt)
  };
}

function sanitizeEnrollment(serialHash, value) {
  const record = value && typeof value === "object" ? value : {};
  return {
    serialHash: safeText(serialHash, 128),
    installationId: safeText(record.installationId, 80),
    hostname: safeText(record.hostname, 120),
    status: safeText(record.status, 40),
    attempts: Number(record.attempts || 0),
    createdAt: isoDate(record.createdAt),
    expiresAt: isoDate(record.expiresAt),
    claimedAt: isoDate(record.claimedAt),
    completedAt: isoDate(record.completedAt),
    ownerUid: safeText(record.ownerUid, 128),
    deviceUid: safeText(record.deviceUid, 128)
  };
}

function memberIndex(memberships) {
  const byInstallation = new Map();
  for (const [uid, installations] of Object.entries(memberships || {})) {
    if (!installations || typeof installations !== "object") continue;
    for (const [installationId, membership] of Object.entries(installations)) {
      if (!membership || typeof membership !== "object") continue;
      const list = byInstallation.get(installationId) || [];
      list.push({
        uid,
        role: safeText(membership.role, 40),
        active: membership.active === true,
        updatedAt: isoDate(membership.updatedAt),
        updatedBy: safeText(membership.updatedBy, 128)
      });
      byInstallation.set(installationId, list);
    }
  }
  for (const list of byInstallation.values()) list.sort((a, b) => a.role.localeCompare(b.role) || a.uid.localeCompare(b.uid));
  return byInstallation;
}

async function listAllUsers(auth, maximum = MAX_USERS) {
  const users = [];
  let pageToken;
  do {
    const remaining = maximum - users.length;
    if (remaining <= 0) break;
    const page = await auth.listUsers(Math.min(1000, remaining), pageToken);
    users.push(...(page.users || []));
    pageToken = page.pageToken;
  } while (pageToken && users.length < maximum);
  return { users, truncated: Boolean(pageToken) };
}

function userSummary(user, memberships) {
  const claims = user.customClaims || {};
  const userMemberships = memberships?.[user.uid] && typeof memberships[user.uid] === "object"
    ? Object.entries(memberships[user.uid]).map(([installationId, membership]) => ({
        installationId,
        role: safeText(membership?.role, 40),
        active: membership?.active === true,
        updatedAt: isoDate(membership?.updatedAt)
      }))
    : [];
  return {
    uid: user.uid,
    email: user.email || null,
    emailVerified: user.emailVerified === true,
    disabled: user.disabled === true,
    displayName: user.displayName || null,
    createdAt: isoDate(user.metadata?.creationTime),
    lastSignInAt: isoDate(user.metadata?.lastSignInTime),
    globalRoles: {
      oryxAdmin: claims.oryxAdmin === true,
      oryxStaff: claims.oryxStaff === true,
      oryxDeveloper: claims.oryxDeveloper === true,
      etrDevice: claims.etrDevice === true
    },
    memberships: userMemberships.sort((a, b) => a.installationId.localeCompare(b.installationId))
  };
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

export function createAdminService({
  db,
  auth,
  adminEmails = process.env.ETR_ADMIN_EMAILS || "amotard.oryx@gmail.com",
  getConnectedInstallations = () => [],
  now = () => Date.now()
}) {
  if (!db?.ref || !auth?.getUser || !auth?.listUsers || !auth?.setCustomUserClaims) {
    throw new Error("EtR admin service requires Firebase Admin Database and Auth");
  }
  const bootstrapEmails = new Set(
    String(adminEmails || "")
      .split(",")
      .map((email) => email.trim().toLowerCase())
      .filter(Boolean)
  );
  if (!bootstrapEmails.size) throw new Error("ETR_ADMIN_EMAILS must contain at least one verified address");

  async function writeAudit(record) {
    await db.ref("adminAudit").push(record);
  }

  async function ensureSession(decoded) {
    const level = adminLevel(decoded);
    if (level !== "none") {
      return {
        authorized: true,
        level,
        refreshRequired: false,
        uid: decoded.uid,
        email: decoded.email || null,
        recentAuthentication: recentAuthentication(decoded, now)
      };
    }
    const email = String(decoded?.email || "").trim().toLowerCase();
    if (decoded?.email_verified !== true || !bootstrapEmails.has(email)) {
      throw httpError(403, "admin/access-denied", "Compte non autorisé pour l’administration EtR");
    }
    const user = await auth.getUser(decoded.uid);
    const existingClaims = user.customClaims || {};
    if (existingClaims.oryxAdmin !== true || existingClaims.oryxStaff !== true) {
      await auth.setCustomUserClaims(decoded.uid, {
        ...existingClaims,
        oryxAdmin: true,
        oryxStaff: true
      });
      await db.ref(`adminProfiles/${decoded.uid}`).set({
        email,
        role: "superadmin",
        active: true,
        grantedAt: new Date(now()).toISOString(),
        grantedBy: "bootstrap-email-allowlist"
      });
      await writeAudit(auditRecord({
        actor: decoded,
        action: "admin.bootstrap",
        targetType: "user",
        targetId: decoded.uid,
        reason: "Adresse administrateur EtR vérifiée",
        before: existingClaims,
        after: { ...existingClaims, oryxAdmin: true, oryxStaff: true },
        now
      }));
    }
    return {
      authorized: true,
      level: "write",
      refreshRequired: true,
      uid: decoded.uid,
      email,
      recentAuthentication: recentAuthentication(decoded, now)
    };
  }

  function requireRead(decoded) {
    const level = adminLevel(decoded);
    if (level === "none") throw httpError(403, "admin/access-denied", "Accès administrateur requis");
    return level;
  }

  function requireWrite(decoded) {
    if (adminLevel(decoded) !== "write") throw httpError(403, "admin/write-denied", "Droit administrateur en écriture requis");
    if (!recentAuthentication(decoded, now)) {
      throw httpError(401, "admin/recent-auth-required", "Reconnectez-vous avant cette opération sensible");
    }
  }

  async function overview(decoded) {
    requireRead(decoded);
    const connected = new Set(getConnectedInstallations());
    const [installationsSnap, membershipsSnap, enrollmentSnap, usersPage] = await Promise.all([
      db.ref("installations").get(),
      db.ref("memberships").get(),
      db.ref("enrollmentRequests").get(),
      listAllUsers(auth, MAX_USERS)
    ]);
    const installations = objectValue(installationsSnap);
    const memberships = objectValue(membershipsSnap);
    const enrollments = objectValue(enrollmentSnap);
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
        total: usersPage.users.length,
        disabled: usersPage.users.filter((user) => user.disabled).length,
        truncated: usersPage.truncated
      },
      memberships: { active: activeMemberships },
      enrollments: {
        pending: Object.values(enrollments).filter((record) => ["pending", "claimed"].includes(record?.status)).length,
        completed: Object.values(enrollments).filter((record) => record?.status === "completed").length
      }
    };
  }

  async function listInstallations(decoded, limit = MAX_INSTALLATIONS) {
    requireRead(decoded);
    const connected = new Set(getConnectedInstallations());
    const [installationsSnap, membershipsSnap] = await Promise.all([
      db.ref("installations").get(),
      db.ref("memberships").get()
    ]);
    const installations = objectValue(installationsSnap);
    const members = memberIndex(objectValue(membershipsSnap));
    const items = Object.entries(installations).map(([installationId, record]) => ({
      installationId,
      metadata: sanitizeMetadata(record?.metadata),
      latest: summarizeLatest(record?.latest, connected.has(installationId)),
      members: members.get(installationId) || []
    }));
    items.sort((a, b) => String(b.latest.updatedAt || "").localeCompare(String(a.latest.updatedAt || "")) || a.installationId.localeCompare(b.installationId));
    const maximum = listLimit(limit, MAX_INSTALLATIONS);
    return { items: items.slice(0, maximum), total: items.length, truncated: items.length > maximum };
  }

  async function listUsers(decoded, limit = MAX_USERS) {
    requireRead(decoded);
    const memberships = objectValue(await db.ref("memberships").get());
    const page = await listAllUsers(auth, listLimit(limit, MAX_USERS));
    return {
      items: page.users.map((user) => userSummary(user, memberships)).sort((a, b) => String(a.email || a.uid).localeCompare(String(b.email || b.uid))),
      truncated: page.truncated
    };
  }

  async function listEnrollments(decoded, limit = 200) {
    requireRead(decoded);
    const enrollments = objectValue(await db.ref("enrollmentRequests").get());
    const items = Object.entries(enrollments).map(([serialHash, record]) => sanitizeEnrollment(serialHash, record));
    items.sort((a, b) => String(b.createdAt || "").localeCompare(String(a.createdAt || "")));
    const maximum = listLimit(limit, 200);
    return { items: items.slice(0, maximum), total: items.length, truncated: items.length > maximum };
  }

  async function listAudit(decoded, limit = 100) {
    requireRead(decoded);
    const audit = objectValue(await db.ref("adminAudit").get());
    const items = Object.entries(audit).map(([id, record]) => ({
      id,
      at: isoDate(record?.at),
      actorUid: safeText(record?.actorUid, 128),
      actorEmail: safeText(record?.actorEmail, 254),
      action: safeText(record?.action, 120),
      targetType: safeText(record?.targetType, 80),
      targetId: safeText(record?.targetId, 160),
      reason: safeText(record?.reason, 500),
      before: record?.before ?? null,
      after: record?.after ?? null
    }));
    items.sort((a, b) => String(b.at || "").localeCompare(String(a.at || "")));
    const maximum = listLimit(limit, MAX_AUDIT);
    return { items: items.slice(0, maximum), total: items.length, truncated: items.length > maximum };
  }

  async function setMembership(decoded, input) {
    requireWrite(decoded);
    const uid = safeUid(input?.uid);
    const installationId = safeInstallationId(input?.installationId);
    const role = safeText(input?.role, 40, true, "Rôle");
    const active = input?.active === true;
    const reason = safeReason(input?.reason);
    if (!ROLES.has(role)) throw httpError(400, "admin/invalid-role", "Rôle EtR invalide");
    const targetUser = await auth.getUser(uid);
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
      after: { ...after, targetEmail: targetUser.email || null },
      now
    }));
    return { ok: true, membership: after };
  }

  async function updateInstallationMetadata(decoded, input) {
    requireWrite(decoded);
    const installationId = safeInstallationId(input?.installationId);
    const reason = safeReason(input?.reason);
    const path = `installations/${installationId}/metadata`;
    const before = (await db.ref(path).get()).val() || {};
    const after = {
      ...before,
      display_name: safeText(input?.displayName, 160),
      client: safeText(input?.client, 160),
      site: safeText(input?.site, 160),
      updatedAt: new Date(now()).toISOString(),
      updatedBy: decoded.uid
    };
    await db.ref(path).set(after);
    await writeAudit(auditRecord({
      actor: decoded,
      action: "installation.metadata.update",
      targetType: "installation",
      targetId: installationId,
      reason,
      before: sanitizeMetadata(before),
      after: sanitizeMetadata(after),
      now
    }));
    return { ok: true, metadata: sanitizeMetadata(after) };
  }

  async function transferOwner(decoded, input) {
    requireWrite(decoded);
    const installationId = safeInstallationId(input?.installationId);
    const uid = safeUid(input?.uid);
    const reason = safeReason(input?.reason);
    if (safeText(input?.confirmation, 80) !== installationId) {
      throw httpError(400, "admin/confirmation-required", "Recopiez l’identifiant de l’installation pour confirmer");
    }
    const targetUser = await auth.getUser(uid);
    if (!targetUser.emailVerified) throw httpError(409, "admin/email-not-verified", "Le nouveau propriétaire doit avoir une adresse vérifiée");
    const memberships = objectValue(await db.ref("memberships").get());
    const updates = {};
    const beforeOwners = [];
    const updatedAt = new Date(now()).toISOString();
    for (const [memberUid, installations] of Object.entries(memberships)) {
      const membership = installations?.[installationId];
      if (membership?.role === "owner" && membership?.active === true) {
        beforeOwners.push({ uid: memberUid, ...membership });
        const replacement = { ...membership, active: false, updatedAt, updatedBy: decoded.uid };
        updates[`memberships/${memberUid}/${installationId}`] = replacement;
        updates[`userInstallations/${memberUid}/${installationId}`] = replacement;
      }
    }
    const newOwner = { role: "owner", active: true, updatedAt, updatedBy: decoded.uid };
    updates[`memberships/${uid}/${installationId}`] = newOwner;
    updates[`userInstallations/${uid}/${installationId}`] = newOwner;
    updates[`installations/${installationId}/metadata/owner_uid`] = uid;
    updates[`installations/${installationId}/metadata/owner_email`] = targetUser.email || null;
    updates[`installations/${installationId}/metadata/updatedAt`] = updatedAt;
    updates[`installations/${installationId}/metadata/updatedBy`] = decoded.uid;
    await db.ref().update(updates);
    await writeAudit(auditRecord({
      actor: decoded,
      action: "installation.owner.transfer",
      targetType: "installation",
      targetId: installationId,
      reason,
      before: { owners: beforeOwners },
      after: { ownerUid: uid, ownerEmail: targetUser.email || null },
      now
    }));
    return { ok: true, owner: { uid, email: targetUser.email || null } };
  }

  async function setUserStatus(decoded, input) {
    requireWrite(decoded);
    const uid = safeUid(input?.uid);
    const disabled = input?.disabled === true;
    const reason = safeReason(input?.reason);
    if (uid === decoded.uid) throw httpError(409, "admin/self-protection", "Vous ne pouvez pas désactiver votre propre compte");
    const beforeUser = await auth.getUser(uid);
    const confirmationExpected = beforeUser.email || uid;
    if (safeText(input?.confirmation, 254) !== confirmationExpected) {
      throw httpError(400, "admin/confirmation-required", "Recopiez l’adresse e-mail ou l’UID pour confirmer");
    }
    const afterUser = await auth.updateUser(uid, { disabled });
    if (disabled) await auth.revokeRefreshTokens(uid);
    await writeAudit(auditRecord({
      actor: decoded,
      action: disabled ? "user.disable" : "user.enable",
      targetType: "user",
      targetId: uid,
      reason,
      before: { email: beforeUser.email || null, disabled: beforeUser.disabled === true },
      after: { email: afterUser.email || null, disabled: afterUser.disabled === true },
      now
    }));
    return { ok: true, user: { uid, email: afterUser.email || null, disabled: afterUser.disabled === true } };
  }

  async function revokeSessions(decoded, input) {
    requireWrite(decoded);
    const uid = safeUid(input?.uid);
    const reason = safeReason(input?.reason);
    if (uid === decoded.uid) throw httpError(409, "admin/self-protection", "Utilisez une reconnexion normale pour votre propre compte");
    const user = await auth.getUser(uid);
    const confirmationExpected = user.email || uid;
    if (safeText(input?.confirmation, 254) !== confirmationExpected) {
      throw httpError(400, "admin/confirmation-required", "Recopiez l’adresse e-mail ou l’UID pour confirmer");
    }
    await auth.revokeRefreshTokens(uid);
    await writeAudit(auditRecord({
      actor: decoded,
      action: "user.sessions.revoke",
      targetType: "user",
      targetId: uid,
      reason,
      before: { email: user.email || null },
      after: { revokedAt: new Date(now()).toISOString() },
      now
    }));
    return { ok: true };
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

  app.get("/api/admin/session", async (req, res) => {
    try {
      res.setHeader("Cache-Control", "no-store");
      return res.json(await service.ensureSession(await decoded(req)));
    } catch (error) {
      return sendError(res, error);
    }
  });

  app.get("/api/admin/overview", async (req, res) => {
    try {
      res.setHeader("Cache-Control", "no-store");
      return res.json(await service.overview(await decoded(req)));
    } catch (error) {
      return sendError(res, error);
    }
  });

  app.get("/api/admin/installations", async (req, res) => {
    try {
      res.setHeader("Cache-Control", "no-store");
      return res.json(await service.listInstallations(await decoded(req), req.query?.limit));
    } catch (error) {
      return sendError(res, error);
    }
  });

  app.get("/api/admin/users", async (req, res) => {
    try {
      res.setHeader("Cache-Control", "no-store");
      return res.json(await service.listUsers(await decoded(req), req.query?.limit));
    } catch (error) {
      return sendError(res, error);
    }
  });

  app.get("/api/admin/enrollments", async (req, res) => {
    try {
      res.setHeader("Cache-Control", "no-store");
      return res.json(await service.listEnrollments(await decoded(req), req.query?.limit));
    } catch (error) {
      return sendError(res, error);
    }
  });

  app.get("/api/admin/audit", async (req, res) => {
    try {
      res.setHeader("Cache-Control", "no-store");
      return res.json(await service.listAudit(await decoded(req), req.query?.limit));
    } catch (error) {
      return sendError(res, error);
    }
  });

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

export const ADMIN_POLICY = Object.freeze({
  roles: [...ROLES],
  recentAuthenticationSeconds: RECENT_AUTH_SECONDS,
  maximumUsers: MAX_USERS,
  maximumInstallations: MAX_INSTALLATIONS,
  maximumAuditEntries: MAX_AUDIT
});
