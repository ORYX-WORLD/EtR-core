import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";
import { createDeviceBootstrapService } from "./device-bootstrap.mjs";
import { serialFingerprint } from "./enrollment.mjs";

function memoryDatabase() {
  const values = new Map();
  return {
    values,
    ref(path = "") {
      return {
        async get() {
          return { val: () => values.get(path) ?? null };
        },
        async transaction(update) {
          const current = values.get(path) ?? null;
          const next = update(structuredClone(current));
          if (next === undefined) {
            return { committed: false, snapshot: { val: () => structuredClone(current) } };
          }
          values.set(path, structuredClone(next));
          return { committed: true, snapshot: { val: () => structuredClone(next) } };
        }
      };
    }
  };
}

test("issues a one-time factory ticket only to the authorized bench EtR", async () => {
  const timestamp = Date.now();
  const database = memoryDatabase();
  const service = createDeviceBootstrapService({
    db: database,
    now: () => timestamp,
    factoryInstallations: ["etr-factory-bench"],
    randomBytes: () => Buffer.alloc(32, 7)
  });
  await assert.rejects(
    service.issueFactoryTicket({
      decodedUser: { uid: "device-other", etrDevice: true, installationId: "etr-field-unit" }
    }),
    error => error.code === "factory_device_refused" && error.status === 403
  );
  const issued = await service.issueFactoryTicket({
    decodedUser: { uid: "device-factory", etrDevice: true, installationId: "etr-factory-bench" },
    expiresIn: 3600
  });
  assert.equal(issued.status, "issued");
  assert.match(issued.ticket, /^[A-Za-z0-9_-]{40,120}$/);
  const stored = [...database.values.entries()].find(([path]) => path.startsWith("factoryBootstrapTickets/"));
  assert.ok(stored);
  assert.equal(stored[1].status, "issued");
  assert.equal(stored[1].issuedByInstallationId, "etr-factory-bench");
  assert.equal(JSON.stringify(stored[1]).includes(issued.ticket), false);
});

test("redeems a factory ticket once and permits an idempotent first-boot retry", async () => {
  const timestamp = Date.now();
  const database = memoryDatabase();
  const service = createDeviceBootstrapService({
    db: database,
    now: () => timestamp,
    factoryInstallations: ["etr-factory-bench"],
    randomBytes: () => Buffer.alloc(32, 9)
  });
  const issued = await service.issueFactoryTicket({
    decodedUser: { uid: "device-factory", etrDevice: true, installationId: "etr-factory-bench" },
    expiresIn: 3600
  });
  const { publicKey } = crypto.generateKeyPairSync("ed25519");
  const publicKeyPem = publicKey.export({ type: "spki", format: "pem" });
  const first = await service.redeemFactoryTicket({
    ticket: issued.ticket,
    serial: "0000ABCD1234EF56",
    installationId: "etr-abcd1234ef56",
    publicKeyPem,
    hostname: "etr-new"
  });
  assert.equal(first.status, "registered");
  const stored = database.values.get(`deviceBootstrap/${serialFingerprint("0000ABCD1234EF56")}`);
  assert.equal(stored.provisioningMode, "factory-ticket");
  assert.equal(stored.factoryInstallationId, "etr-factory-bench");

  const retry = await service.redeemFactoryTicket({
    ticket: issued.ticket,
    serial: "0000ABCD1234EF56",
    publicKeyPem,
    hostname: "etr-abcd1234"
  });
  assert.equal(retry.status, "already_registered");

  const otherKey = crypto.generateKeyPairSync("ed25519").publicKey.export({ type: "spki", format: "pem" });
  await assert.rejects(
    service.redeemFactoryTicket({
      ticket: issued.ticket,
      serial: "0000FFFF1234EEEE",
      publicKeyPem: otherKey
    }),
    error => error.code === "factory_ticket_used" && error.status === 409
  );
});

test("rejects an expired factory ticket before registering a device", async () => {
  let timestamp = Date.now();
  const database = memoryDatabase();
  const service = createDeviceBootstrapService({
    db: database,
    now: () => timestamp,
    factoryInstallations: ["etr-factory-bench"],
    randomBytes: () => Buffer.alloc(32, 11)
  });
  const issued = await service.issueFactoryTicket({
    decodedUser: { uid: "device-factory", etrDevice: true, installationId: "etr-factory-bench" },
    expiresIn: 600
  });
  timestamp += 601_000;
  const publicKeyPem = crypto.generateKeyPairSync("ed25519").publicKey.export({ type: "spki", format: "pem" });
  await assert.rejects(
    service.redeemFactoryTicket({
      ticket: issued.ticket,
      serial: "0000ABCD1234EF56",
      publicKeyPem
    }),
    error => error.code === "factory_ticket_expired" && error.status === 410
  );
});
