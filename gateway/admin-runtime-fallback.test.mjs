import assert from "node:assert/strict";
import test from "node:test";
import { createAdminService } from "./admin.mjs";

function clone(value) {
  return value === undefined ? undefined : structuredClone(value);
}

function parts(path) {
  return String(path || "").split("/").filter(Boolean);
}

function getAt(root, path) {
  let current = root;
  for (const part of parts(path)) {
    if (!current || typeof current !== "object") return null;
    current = current[part];
  }
  return current === undefined ? null : current;
}

function setAt(root, path, value) {
  const pathParts = parts(path);
  let current = root;
  for (const part of pathParts.slice(0, -1)) {
    if (!current[part] || typeof current[part] !== "object") current[part] = {};
    current = current[part];
  }
  current[pathParts.at(-1)] = clone(value);
}

function snapshot(value) {
  return {
    val: () => clone(value),
    child(path) { return snapshot(getAt(value || {}, path)); }
  };
}

function database(initial = {}) {
  const data = clone(initial);
  let counter = 0;
  return {
    data,
    ref(path = "") {
      return {
        async get() { return snapshot(getAt(data, path)); },
        async set(value) { setAt(data, path, value); },
        async update(values) {
          for (const [relative, value] of Object.entries(values || {})) {
            setAt(data, [path, relative].filter(Boolean).join("/"), value);
          }
        },
        async push(value) {
          const key = `audit-${++counter}`;
          setAt(data, [path, key].filter(Boolean).join("/"), value);
          return { key };
        }
      };
    }
  };
}

function unavailableAuth() {
  const denied = () => {
    throw Object.assign(new Error("Insufficient permission"), {
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

const NOW = Date.parse("2026-08-02T19:30:00.000Z");
const admin = {
  uid: "admin-uid",
  email: "amotard.oryx@gmail.com",
  email_verified: true,
  auth_time: Math.floor(NOW / 1000) - 30
};

function fixture() {
  const db = database({
    installations: {
      "etr-core": {
        metadata: {
          display_name: "EtR core",
          owner_uid: "owner-uid",
          owner_email: "owner@example.com"
        },
        latest: { updated_at: "2026-08-02T19:29:00.000Z" }
      }
    },
    memberships: {
      "owner-uid": {
        "etr-core": { role: "owner", active: true }
      }
    },
    userInstallations: {},
    enrollmentRequests: {
      serial: { installationId: "etr-core", status: "pending", attempts: 1 }
    },
    adminProfiles: {}
  });
  const service = createAdminService({
    db,
    auth: unavailableAuth(),
    adminEmails: "amotard.oryx@gmail.com",
    getConnectedInstallations: () => ["etr-core"],
    now: () => NOW
  });
  return { db, service };
}

test("admits the verified ORYX allowlist when Firebase Auth Admin is unavailable", async () => {
  const { db, service } = fixture();
  const session = await service.ensureSession(admin);
  assert.equal(session.authorized, true);
  assert.equal(session.level, "write");
  assert.equal(session.refreshRequired, false);
  assert.equal(session.authorizationSource, "verified-email-allowlist");
  assert.equal(session.capabilities.firebaseAuthAdmin, false);
  assert.equal(session.capabilities.manageMemberships, true);
  assert.equal(db.data.adminProfiles["admin-uid"].role, "superadmin");
  assert.equal(Object.values(db.data.adminAudit)[0].action, "admin.allowlist.activate");
});

test("keeps the administration overview available through the database index", async () => {
  const { service } = fixture();
  const overview = await service.overview(admin);
  assert.equal(overview.installations.total, 1);
  assert.equal(overview.installations.connected, 1);
  assert.equal(overview.users.total, 2);
  assert.equal(overview.users.source, "realtime-database-index");
  assert.equal(overview.users.authManagementAvailable, false);
  assert.equal(overview.memberships.active, 1);
  assert.equal(overview.enrollments.pending, 1);
});

test("returns a safe database-backed user directory instead of an internal error", async () => {
  const { service } = fixture();
  await service.ensureSession(admin);
  const result = await service.listUsers(admin);
  assert.equal(result.source, "realtime-database-index");
  assert.equal(result.authManagementAvailable, false);
  assert.deepEqual(result.items.map((item) => item.uid), ["admin-uid", "owner-uid"]);
  assert.equal(result.items[0].globalRoles.oryxAdmin, true);
});

test("allows membership administration without Firebase Auth user lookup", async () => {
  const { db, service } = fixture();
  const result = await service.setMembership(admin, {
    uid: "new-user-uid",
    installationId: "etr-core",
    role: "maintenance",
    active: true,
    reason: "Accès maintenance validé par ORYX"
  });
  assert.equal(result.ok, true);
  assert.equal(result.authManagementAvailable, false);
  assert.equal(db.data.memberships["new-user-uid"]["etr-core"].role, "maintenance");
  assert.equal(db.data.userInstallations["new-user-uid"]["etr-core"].active, true);
});

test("returns an explicit limited-capability error for Firebase account security operations", async () => {
  const { service } = fixture();
  await assert.rejects(
    service.setUserStatus(admin, {
      uid: "owner-uid",
      disabled: true,
      reason: "Compte à désactiver",
      confirmation: "owner@example.com"
    }),
    (error) => error.status === 409 && error.code === "admin/firebase-auth-management-unavailable"
  );
});

test("still rejects a verified account outside the ORYX allowlist", async () => {
  const { service } = fixture();
  await assert.rejects(
    service.ensureSession({
      ...admin,
      uid: "other-uid",
      email: "other@example.com"
    }),
    (error) => error.status === 403 && error.code === "admin/access-denied"
  );
});
