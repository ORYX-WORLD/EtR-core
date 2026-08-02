import assert from "node:assert/strict";
import test from "node:test";
import { createAdminService } from "./admin.mjs";

function clone(value) {
  return value === undefined ? undefined : structuredClone(value);
}

function segments(path) {
  return String(path || "").split("/").filter(Boolean);
}

function getAt(root, path) {
  let current = root;
  for (const segment of segments(path)) {
    if (!current || typeof current !== "object") return null;
    current = current[segment];
  }
  return current === undefined ? null : current;
}

function setAt(root, path, value) {
  const parts = segments(path);
  if (!parts.length) throw new Error("root set not supported");
  let current = root;
  for (const segment of parts.slice(0, -1)) {
    if (!current[segment] || typeof current[segment] !== "object") current[segment] = {};
    current = current[segment];
  }
  current[parts.at(-1)] = clone(value);
}

function snapshot(value) {
  return {
    val: () => clone(value),
    child(path) { return snapshot(getAt(value || {}, path)); }
  };
}

function databaseFixture(initial = {}) {
  const data = clone(initial);
  let pushCounter = 0;
  return {
    data,
    ref(path = "") {
      return {
        async get() { return snapshot(getAt(data, path)); },
        async set(value) { setAt(data, path, value); },
        async update(updates) {
          for (const [relative, value] of Object.entries(updates || {})) {
            const target = [path, relative].filter(Boolean).join("/");
            setAt(data, target, value);
          }
        },
        async push(value) {
          const id = `audit-${String(++pushCounter).padStart(3, "0")}`;
          setAt(data, [path, id].filter(Boolean).join("/"), value);
          return { key: id };
        }
      };
    }
  };
}

function user(uid, email, overrides = {}) {
  return {
    uid,
    email,
    emailVerified: true,
    disabled: false,
    displayName: null,
    customClaims: {},
    metadata: {
      creationTime: "2026-07-01T10:00:00.000Z",
      lastSignInTime: "2026-07-26T09:55:00.000Z"
    },
    ...overrides
  };
}

function authFixture(initialUsers) {
  const users = new Map(initialUsers.map((entry) => [entry.uid, clone(entry)]));
  const revoked = [];
  return {
    users,
    revoked,
    async getUser(uid) {
      const entry = users.get(uid);
      if (!entry) throw Object.assign(new Error("not found"), { code: "auth/user-not-found" });
      return clone(entry);
    },
    async listUsers(maxResults, pageToken) {
      const all = [...users.values()];
      const start = Number(pageToken || 0);
      const selected = all.slice(start, start + maxResults).map(clone);
      const next = start + selected.length < all.length ? String(start + selected.length) : undefined;
      return { users: selected, pageToken: next };
    },
    async setCustomUserClaims(uid, claims) {
      const entry = users.get(uid);
      entry.customClaims = clone(claims);
    },
    async updateUser(uid, updates) {
      const entry = users.get(uid);
      Object.assign(entry, clone(updates));
      return clone(entry);
    },
    async revokeRefreshTokens(uid) { revoked.push(uid); }
  };
}

function restrictedAuthFixture() {
  const denied = async () => {
    throw Object.assign(new Error("Insufficient permission to manage Firebase users"), {
      code: "auth/insufficient-permission"
    });
  };
  return {
    getUser: denied,
    listUsers: denied,
    setCustomUserClaims: denied,
    updateUser: denied,
    revokeRefreshTokens: denied
  };
}

const NOW = Date.parse("2026-07-26T10:00:00.000Z");
const recentAdmin = {
  uid: "admin-uid",
  email: "amotard.oryx@gmail.com",
  email_verified: true,
  oryxAdmin: true,
  oryxStaff: true,
  auth_time: Math.floor(NOW / 1000) - 60
};
const recentAllowlistedAdmin = {
  uid: "admin-uid",
  email: "amotard.oryx@gmail.com",
  email_verified: true,
  auth_time: Math.floor(NOW / 1000) - 60
};

function initialDatabase() {
  return {
    installations: {
      "etr-site-001": {
        metadata: {
          display_name: "Centrale principale",
          client: "Client A",
          site: "Toulouse",
          owner_uid: "owner-uid",
          owner_email: "owner@example.com",
          device_uid: "etrdev_001",
          device_fingerprint: "fingerprint-001",
          enrolled_at: "2026-07-20T08:00:00.000Z"
        },
        latest: {
          updated_at: "2026-07-26T09:59:40.000Z",
          bridge_online: true,
          local_api_online: true,
          hostname: "etr-core",
          measurements: { pressure_bar: 28.1 },
          states: { compressor_running: true },
          alerts: []
        }
      }
    },
    memberships: {
      "owner-uid": {
        "etr-site-001": { role: "owner", active: true }
      }
    },
    userInstallations: {},
    enrollmentRequests: {
      "serial-hash-001": {
        installationId: "etr-site-001",
        hostname: "etr-core",
        status: "pending",
        attempts: 1,
        codeHash: "must-never-be-returned",
        rotationTokenHash: "must-never-be-returned",
        createdAt: "2026-07-26T09:00:00.000Z",
        expiresAt: "2026-07-27T09:00:00.000Z"
      }
    }
  };
}

function fixture() {
  const db = databaseFixture(initialDatabase());
  const auth = authFixture([
    user("admin-uid", "amotard.oryx@gmail.com", { customClaims: { oryxAdmin: true, oryxStaff: true } }),
    user("owner-uid", "owner@example.com"),
    user("target-uid", "target@example.com")
  ]);
  const service = createAdminService({
    db,
    auth,
    adminEmails: "amotard.oryx@gmail.com",
    getConnectedInstallations: () => ["etr-site-001"],
    now: () => NOW
  });
  return { db, auth, service };
}

test("authorizes the verified allowlisted administrator without requiring Auth Admin", async () => {
  const db = databaseFixture();
  const auth = authFixture([user("admin-uid", "amotard.oryx@gmail.com")]);
  const service = createAdminService({ db, auth, adminEmails: "amotard.oryx@gmail.com", now: () => NOW });
  const session = await service.ensureSession(recentAllowlistedAdmin);
  assert.equal(session.authorized, true);
  assert.equal(session.level, "write");
  assert.equal(session.refreshRequired, false);
  assert.equal(session.authorizationSource, "verified-email-allowlist");
  assert.deepEqual(auth.users.get("admin-uid").customClaims, {});
  assert.equal(db.data.adminProfiles["admin-uid"].role, "superadmin");
  const audit = Object.values(db.data.adminAudit);
  assert.equal(audit.length, 1);
  assert.equal(audit[0].action, "admin.allowlist.authorize");
});

test("does not duplicate the allowlist audit on every session", async () => {
  const db = databaseFixture();
  const auth = authFixture([user("admin-uid", "amotard.oryx@gmail.com")]);
  const service = createAdminService({ db, auth, adminEmails: "amotard.oryx@gmail.com", now: () => NOW });
  await service.ensureSession(recentAllowlistedAdmin);
  await service.ensureSession(recentAllowlistedAdmin);
  assert.equal(Object.values(db.data.adminAudit).length, 1);
});

test("rejects a verified account outside the administrator allowlist", async () => {
  const db = databaseFixture();
  const auth = authFixture([user("intruder", "intruder@example.com")]);
  const service = createAdminService({ db, auth, adminEmails: "amotard.oryx@gmail.com", now: () => NOW });
  await assert.rejects(
    service.ensureSession({ uid: "intruder", email: "intruder@example.com", email_verified: true, auth_time: Math.floor(NOW / 1000) }),
    (error) => error.status === 403 && error.code === "admin/access-denied"
  );
});

test("returns a sanitized overview, fleet and enrollment queue", async () => {
  const { service } = fixture();
  const overview = await service.overview(recentAdmin);
  assert.equal(overview.installations.total, 1);
  assert.equal(overview.installations.connected, 1);
  assert.equal(overview.users.total, 3);
  assert.equal(overview.users.managementAvailable, true);
  assert.equal(overview.enrollments.pending, 1);

  const fleet = await service.listInstallations(recentAdmin);
  assert.equal(fleet.items[0].installationId, "etr-site-001");
  assert.equal(fleet.items[0].latest.connected, true);
  assert.equal(fleet.items[0].latest.measurementCount, 1);
  assert.equal(fleet.items[0].members[0].role, "owner");

  const enrollments = await service.listEnrollments(recentAdmin);
  assert.equal(enrollments.items[0].status, "pending");
  const serialized = JSON.stringify(enrollments);
  assert.equal(serialized.includes("must-never-be-returned"), false);
  assert.equal(serialized.includes("codeHash"), false);
  assert.equal(serialized.includes("rotationTokenHash"), false);
});

test("keeps the fleet admin available when Firebase user management permission is absent", async () => {
  const db = databaseFixture(initialDatabase());
  const service = createAdminService({
    db,
    auth: restrictedAuthFixture(),
    adminEmails: "amotard.oryx@gmail.com",
    getConnectedInstallations: () => ["etr-site-001"],
    now: () => NOW
  });

  const session = await service.ensureSession(recentAllowlistedAdmin);
  assert.equal(session.authorized, true);
  assert.equal(session.level, "write");

  const overview = await service.overview(recentAllowlistedAdmin);
  assert.equal(overview.installations.total, 1);
  assert.equal(overview.users.managementAvailable, false);
  assert.equal(overview.users.source, "realtime-database-index");
  assert.equal(overview.capabilities.fleetAdministration, true);

  const users = await service.listUsers(recentAllowlistedAdmin);
  assert.equal(users.limited, true);
  assert.equal(users.managementAvailable, false);
  assert.equal(users.items.some((entry) => entry.uid === "owner-uid"), true);

  const membership = await service.setMembership(recentAllowlistedAdmin, {
    uid: "owner-uid",
    installationId: "etr-site-001",
    role: "maintenance",
    active: true,
    reason: "Mise à jour du rôle depuis le back-office ORYX"
  });
  assert.equal(membership.membership.role, "maintenance");

  await assert.rejects(
    service.transferOwner(recentAllowlistedAdmin, {
      installationId: "etr-site-001",
      uid: "owner-uid",
      reason: "Transfert de propriétaire demandé",
      confirmation: "etr-site-001"
    }),
    (error) => error.status === 503 && error.code === "admin/auth-management-unavailable"
  );
});

test("writes a membership to both indexes and audits the change", async () => {
  const { db, service } = fixture();
  const result = await service.setMembership(recentAdmin, {
    uid: "target-uid",
    installationId: "etr-site-001",
    role: "maintenance",
    active: true,
    reason: "Accès maintenance demandé par le client"
  });
  assert.equal(result.membership.role, "maintenance");
  assert.equal(db.data.memberships["target-uid"]["etr-site-001"].active, true);
  assert.equal(db.data.userInstallations["target-uid"]["etr-site-001"].role, "maintenance");
  assert.equal(Object.values(db.data.adminAudit).at(-1).action, "membership.set");
});

test("rejects sensitive writes when the administrator authentication is old", async () => {
  const { service } = fixture();
  await assert.rejects(
    service.setMembership({ ...recentAdmin, auth_time: Math.floor(NOW / 1000) - 3600 }, {
      uid: "target-uid",
      installationId: "etr-site-001",
      role: "viewer",
      active: true,
      reason: "Accès lecture demandé"
    }),
    (error) => error.status === 401 && error.code === "admin/recent-auth-required"
  );
});

test("transfers ownership atomically after explicit installation confirmation", async () => {
  const { db, service } = fixture();
  const result = await service.transferOwner(recentAdmin, {
    installationId: "etr-site-001",
    uid: "target-uid",
    reason: "Transfert validé par le contrat client",
    confirmation: "etr-site-001"
  });
  assert.equal(result.owner.uid, "target-uid");
  assert.equal(db.data.memberships["owner-uid"]["etr-site-001"].active, false);
  assert.equal(db.data.memberships["target-uid"]["etr-site-001"].role, "owner");
  assert.equal(db.data.installations["etr-site-001"].metadata.owner_uid, "target-uid");
  assert.equal(Object.values(db.data.adminAudit).at(-1).action, "installation.owner.transfer");
});

test("protects the administrator from disabling or revoking their own account", async () => {
  const { service } = fixture();
  await assert.rejects(
    service.setUserStatus(recentAdmin, {
      uid: "admin-uid",
      disabled: true,
      reason: "Test de protection du compte",
      confirmation: "amotard.oryx@gmail.com"
    }),
    (error) => error.code === "admin/self-protection"
  );
  await assert.rejects(
    service.revokeSessions(recentAdmin, {
      uid: "admin-uid",
      reason: "Test de protection du compte",
      confirmation: "amotard.oryx@gmail.com"
    }),
    (error) => error.code === "admin/self-protection"
  );
});

test("disables another user, revokes sessions and records the action", async () => {
  const { auth, db, service } = fixture();
  const result = await service.setUserStatus(recentAdmin, {
    uid: "target-uid",
    disabled: true,
    reason: "Compte client clôturé à sa demande",
    confirmation: "target@example.com"
  });
  assert.equal(result.user.disabled, true);
  assert.deepEqual(auth.revoked, ["target-uid"]);
  assert.equal(Object.values(db.data.adminAudit).at(-1).action, "user.disable");
});

test("allows ORYX staff to read but not to modify", async () => {
  const { service } = fixture();
  const staff = { uid: "staff", email: "staff@oryx.fr", email_verified: true, oryxStaff: true, auth_time: Math.floor(NOW / 1000) };
  const users = await service.listUsers(staff);
  assert.equal(users.items.length, 3);
  await assert.rejects(
    service.setMembership(staff, {
      uid: "target-uid",
      installationId: "etr-site-001",
      role: "viewer",
      active: true,
      reason: "Tentative écriture staff"
    }),
    (error) => error.code === "admin/write-denied"
  );
});
