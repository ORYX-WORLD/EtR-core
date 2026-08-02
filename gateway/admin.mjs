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

function normalizedEmail(value) {
  return String(value || "").trim().toLowerCase();
}

function claimsAdminLevel(decoded) {
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

function userMemberships(uid, memberships) {
  return memberships?.[uid] && typeof memberships[uid] === "object"
    ? Object.entries(memberships[uid]).map(([installationId, membership]) => ({
        installationId,
        role: safeText(membership?.role, 40),
        active: membership?.active === true,
        updatedAt: isoDate(membership?.updatedAt)
      })).sort((a, b) => a.installationId.localeCompare(b.installationId))
    : [];
}

function userSummary(user, memberships) {
  const claims = user.customClaims || {};
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
    memberships: userMemberships(user.uid, memberships),
    identitySource: "firebase-auth"
  };
}

function knownUserIndex({ memberships = {}, adminProfiles = {}, installations = {}, enrollments = {} }) {
  const users = new Map();
  const ensure = (uid, values = {}) => {
    const safe = safeText(uid, 128);
    if (!safe) return;
    const existing = users.get(safe) || {
      uid: safe,
      email: null,
      emailVerified: null,
      disabled: null,
      displayName: null,
      createdAt: null,
      lastSignInAt: null,
      globalRoles: { oryxAdmin: false, oryxStaff: false, oryxDeveloper: false, etrDevice: false },
      memberships: userMemberships(safe, memberships),
      identitySource: "realtime-database-index"
    };
    if (!existing.email && values.email) existing.email = safeText(values.email, 254) || null;
    if (!existing.displayName && values.displayName) existing.displayName = safeText(values.displayName, 160) || null;
    if (values.oryxAdmin === true) existing.globalRoles.oryxAdmin = true;
    if (values.oryxStaff === true) existing.globalRoles.oryxStaff = true;
    if (values.oryxDeveloper === true) existing.globalRoles.oryxDeveloper = true;
    if (values.etrDevice === true) existing.globalRoles.etrDevice = true;
    users.set(safe, existing);
  };

  for (const uid of Object.keys(memberships || {})) ensure(uid);
  for (const [uid, profile] of Object.entries(adminProfiles || {})) {
    ensure(uid, {
      email: profile?.email,
      displayName: profile?.displayName,
      oryxAdmin: profile?.active === true && profile?.role === "superadmin",
      oryxStaff: profile?.active === true
    });
  }
  for (const record of Object.values(installations || {})) {
    const metadata = record?.metadata || {};
    ensure(metadata.owner_uid || metadata.ownerUid, {
      email: metadata.owner_email || metadata.ownerEmail,
      displayName: metadata.owner_display_name || metadata.ownerDisplayName
    });
    ensure(metadata.device_uid || metadata.deviceUid, {
      displayName: metadata.display_name || metadata.displayName,
      etrDevice: true
    });
  }
  for (const record of Object.values(enrollments || {})) {
    ensure(record?.ownerUid);
    ensure(record?.deviceUid, { etrDevice: true });
  }
  return users;
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

function isAuthPermissionFailure(error) {
  const code = String(error?.code || "").toLowerCase();
  const message = String(error?.message || error || "").toLowerCase();
  return code === "auth/insufficient-permission" ||
    code === "auth/insufficient-permissions" ||
    message.includes("insufficient permission") ||
    message.includes("permission denied") ||
    message.includes("requires a permission");
}

export function createAdminService({
  db,
  auth,
  adminEmails = process.env.ETR_ADMIN_EMAILS || "amotard.oryx@gmail.com",
  getConnectedInstallations = () => [],
  now = () => Date.now()
}) {
  if (!db?.ref) throw new Error("EtR admin service requires Firebase Admin Database");
  const bootstrapEmails = new Set(
    String(adminEmails || "")
      .split(",")
      .map((email) => email.trim().toLowerCase())
      .filter(Boolean)
  );
  if (!bootstrapEmails.size) throw new Error("ETR_ADMIN_EMAILS must contain at least one verified address");

  const authState = {
    available: Boolean(auth?.getUser && auth?.listUsers && auth?.updateUser && auth?.revokeRefreshTokens),
    code: null
  };

  function allowlisted(decoded) {
    return decoded?.email_verified === true && bootstrapEmails.has(normalizedEmail(decoded?.email));
  }

  function accessLevel(decoded) {
    const claimed = claimsAdminLevel(decoded);
    if (claimed !== "none") return claimed;
    return allowlisted(decoded) ? "write" : "none";
  }

  function markAuthUnavailable(error) {
    authState.available = false;
    authState.code = String(error?.code || "auth/insufficient-permission").slice(0, 120);
  }

  function authUnavailableError() {
    return httpError(
      503,
      "admin/auth-management-unavailable",
      "La gestion des comptes Firebase n’est pas encore autorisée sur le serveur. Les installations et les droits EtR restent administrables."
    );
  }

  async function bestEffortListUsers(maximum = MAX_USERS) {
    if (!authState.available || typeof auth?.listUsers !== "function") {
      return { available: false, users: [], truncated: false };
    }
    try {
      const page = await listAllUsers(auth, maximum);
      return { available: true, ...page };
    } catch (error) {
      if (!isAuthPermissionFailure(error)) throw error;
      markAuthUnavailable(error);
      return { available: false, users: [], truncated: false };
    }
  }

  async function bestEffortGetUser(uid) {
    if (!authState.available || typeof auth?.getUser !== "function") return { available: false, user: null };
    try {
      return { available: true, user: await auth.getUser(uid) };
    } catch (error) {
      if (!isAuthPermissionFailure(error)) throw error;
      markAuthUnavailable(error);
      return { available: false, user: null };
    }
  }

  async function knownUser(uid) {
    const [membershipsSnap, adminProfilesSnap, installationsSnap, enrollmentsSnap] = await Promise.all([
      db.ref("memberships").get(),
      db.ref("adminProfiles").get(),
      db.ref("installations").get(),
      db.ref("enrollmentRequests").get()
    ]);
    return knownUserIndex({
      memberships: objectValue(membershipsSnap),
      adminProfiles: objectValue(adminProfilesSnap),
      installations: objectValue(installationsSnap),
      enrollments: objectValue(enrollmentsSnap)
    }).get(uid) || null;
  }

  async function writeAudit(record) {
    await db.ref("adminAudit").push(record);
  }

  async function ensureAllowlistProfile(decoded) {
    if (!allowlisted(decoded)) return false;
    const email = normalizedEmail(decoded.email);
    const path = `adminProfiles/${decoded.uid}`;
    const before = (await db.ref(path).get()).val() || null;
    const expected = {
      email,
      role: "superadmin",
      active: true,
      grantedAt: before?.grantedAt || new Date(now()).toISOString(),
      grantedBy: before?.grantedBy || "verified-email-allowlist"
    };
    const unchanged = before?.email === expected.email && before?.role === expected.role && before?.active === true;
    if (!unchanged) {
      await db.ref(path).set(expected);
      await writeAudit(auditRecord({
        actor: decoded,
        action: "admin.allowlist.authorize",
        targetType: "user",
        targetId: decoded.uid,
        reason: "Adresse administrateur ORYX vérifiée et inscrite dans la liste d’autorisation",
        before,
        after: expected,
        now
      }));
    }
    return true;
  }

  async function ensureSession(decoded) {
    const level = accessLevel(decoded);
    if (level === "none") {
      throw httpError(403, "admin/access-denied", "Compte non autorisé pour l’administration EtR");
    }
    const byAllowlist = claimsAdminLevel(decoded) === "none" && allowlisted(decoded);
    if (byAllowlist) await ensureAllowlistProfile(decoded);
    return {
      authorized: true,
      level,
      refreshRequired: false,
      authorizationSource: byAllowlist ? "verified-email-allowlist" : "firebase-custom-claims",
      uid: decoded.uid,
      email: decoded.email || null,
      recentAuthentication: recentAuthentication(decoded, now),
      capabilities: {
        fleetAdministration: true,
        membershipAdministration: true,
        firebaseUserManagement: authState.available === false ? false : null
      }
    };
  }

  function requireRead(decoded) {
    const level = accessLevel(decoded);
    if (level === "none") throw httpError(403, "admin/access-denied", "Accès administrateur requis");
    return level;
  }

  function requireWrite(decoded) {
    if (accessLevel(decoded) !== "write") throw httpError(403, "admin/write-denied", "Droit administrateur en écriture requis");
    if (!recentAuthentication(decoded, now)) {
      throw httpError(401, "admin/recent-auth-required", "Reconnectez-vous avant cette opération sensible");
    }
  }

  async function overview(decoded) {
    requireRead(decoded);
    const connected = new Set(getConnectedInstallations());
    const [installationsSnap, membershipsSnap, enrollmentSnap, adminProfilesSnap, usersPage] = await Promise.all([
      db.ref("installations").get(),
      db.ref("memberships").get(),
      db.ref("enrollmentRequests").get(),
      db.ref("adminProfiles").get(),
      bestEffortListUsers(MAX_USERS)
    ]);
    const installations = objectValue(installationsSnap);
    const memberships = objectValue(membershipsSnap);
    const enrollments = objectValue(enrollmentSnap);
    const adminProfiles = objectValue(adminProfilesSnap);
    const activeMemberships = Object.values(memberships).reduce((total, list) => {
      if (!list || typeof list !== "object") return total;
      return total + Object.values(list).filter((membership) => membership?.active === true).length;
    }, 0);
    const known = knownUserIndex({ memberships, adminProfiles, installations, enrollments });
    return {
      generatedAt: new Date(now()).toISOString(),
      installations: {
        total: Object.keys(installations).length,
        connected: Object.keys(installations).filter((id) => connected.has(id)).length
      },
      users: {
        total: usersPage.available ? usersPage.users.length : known.size,
        disabled: usersPage.available ? usersPage.users.filter((user) => user.disabled).length : null,
        truncated: usersPage.truncated,
        source: usersPage.available ? "firebase-auth" : "realtime-database-index",
        managementAvailable: usersPage.available
      },
      memberships: { active: activeMemberships },
      enrollments: {
        pending: Object.values(enrollments).filter((record) => ["pending", "claimed"].includes(record?.status)).length,
        completed: Object.values(enrollments).filter((record) => record?.status === "completed").length
      },
      capabilities: {
        fleetAdministration: true,
        membershipAdministration: true,
        firebaseUserManagement: usersPage.available,
        firebaseAuthErrorCode: usersPage.available ? null : authState.code
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
    const [membershipsSnap, adminProfilesSnap, installationsSnap, enrollmentsSnap, page] = await Promise.all([
      db.ref("memberships").get(),
      db.ref("adminProfiles").get(),
      db.ref("installations").get(),
      db.ref("enrollmentRequests").get(),
      bestEffortListUsers(listLimit(limit, MAX_USERS))
    ]);
    const memberships = objectValue(membershipsSnap);
    if (page.available) {
      return {
        items: page.users.map((user) => userSummary(user, memberships)).sort((a, b) => String(a.email || a.uid).localeCompare(String(b.email || b.uid))),
        truncated: page.truncated,
        limited: false,
        managementAvailable: true,
        source: "firebase-auth"
      };
    }
    const known = knownUserIndex({
      memberships,
      adminProfiles: objectValue(adminProfilesSnap),
      installations: objectValue(installationsSnap),
      enrollments: objectValue(enrollmentsSnap)
    });
    const maximum = listLimit(limit, MAX_USERS);
    const items = [...known.values()].sort((a, b) => String(a.email || a.uid).localeCompare(String(b.email || b.uid)));
    return {
      items: items.slice(0, maximum),
      truncated: items.length > maximum,
      limited: true,
      managementAvailable: false,
      source: "realtime-database-index"
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

    const lookup = await bestEffortGetUser(uid);
    const indexed = lookup.available ? null : await knownUser(uid);
    if (!lookup.available && !indexed) {
      throw httpError(409, "admin/user-unknown", "Utilisateur inconnu dans les index EtR. Vérifiez l’UID avant d’attribuer un droit.");
    }
    const targetEmail = lookup.user?.email || indexed?.email || null;
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
      after: {
        ...after,
        targetEmail,
        identitySource: lookup.available ? "firebase-auth" : "realtime-database-index"
      },
      now
    }));
    return { ok: true, membership: after, target: { uid, email: targetEmail } };
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
    const lookup = await bestEffortGetUser(uid);
    if (!lookup.available) throw authUnavailableError();
    const targetUser = lookup.user;
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
    if (!authState.available || typeof auth?.updateUser !== "function" || typeof auth?.revokeRefreshTokens !== "function") {
      throw authUnavailableError();
    }
    const uid = safeUid(input?.uid);
    const disabled = input?.disabled === true;
    const reason = safeReason(input?.reason);
    if (uid === decoded.uid) throw httpError(409, "admin/self-protection", "Vous ne pouvez pas désactiver votre propre compte");
    const lookup = await bestEffortGetUser(uid);
    if (!lookup.available) throw authUnavailableError();
    const beforeUser = lookup.user;
    const confirmationExpected = beforeUser.email || uid;
    if (safeText(input?.confirmation, 254) !== confirmationExpected) {
      throw httpError(400, "admin/confirmation-required", "Recopiez l’adresse e-mail ou l’UID pour confirmer");
    }
    let afterUser;
    try {
      afterUser = await auth.updateUser(uid, { disabled });
      if (disabled) await auth.revokeRefreshTokens(uid);
    } catch (error) {
      if (!isAuthPermissionFailure(error)) throw error;
      markAuthUnavailable(error);
      throw authUnavailableError();
    }
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
    if (!authState.available || typeof auth?.revokeRefreshTokens !== "function") throw authUnavailableError();
    const uid = safeUid(input?.uid);
    const reason = safeReason(input?.reason);
    if (uid === decoded.uid) throw httpError(409, "admin/self-protection", "Utilisez une reconnexion normale pour votre propre compte");
    const lookup = await bestEffortGetUser(uid);
    if (!lookup.available) throw authUnavailableError();
    const user = lookup.user;
    const confirmationExpected = user.email || uid;
    if (safeText(input?.confirmation, 254) !== confirmationExpected) {
      throw httpError(400, "admin/confirmation-required", "Recopiez l’adresse e-mail ou l’UID pour confirmer");
    }
    try {
      await auth.revokeRefreshTokens(uid);
    } catch (error) {
      if (!isAuthPermissionFailure(error)) throw error;
      markAuthUnavailable(error);
      throw authUnavailableError();
    }
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
  maximumAuditEntries: MAX_AUDIT,
  verifiedEmailAllowlistAccess: true,
  gracefulFirebaseAuthDegradation: true
});
