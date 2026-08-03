import assert from "node:assert/strict";
import test from "node:test";
import { createDeviceBootstrapService } from "./device-bootstrap.mjs";

function memoryDatabase(seed = {}) {
  const values = new Map(Object.entries(seed));
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

test("autorise le banc historique sans claim etrDevice quand deviceAccess le lie à etr-core", async () => {
  const timestamp = Date.now();
  const database = memoryDatabase({ "deviceAccess/legacy-bench-uid": "etr-core" });
  const service = createDeviceBootstrapService({
    db: database,
    now: () => timestamp,
    factoryInstallations: ["etr-0000dd7429c2", "etr-core"],
    randomBytes: () => Buffer.alloc(32, 13)
  });

  const issued = await service.issueFactoryTicket({
    decodedUser: { uid: "legacy-bench-uid", email_verified: false },
    expiresIn: 3600
  });

  assert.equal(issued.status, "issued");
  const stored = [...database.values.entries()].find(([path]) => path.startsWith("factoryBootstrapTickets/"));
  assert.ok(stored);
  assert.equal(stored[1].issuedByUid, "legacy-bench-uid");
  assert.equal(stored[1].issuedByInstallationId, "etr-core");
});

test("refuse un compte historique lié à un EtR qui n'est pas un banc autorisé", async () => {
  const database = memoryDatabase({ "deviceAccess/field-device-uid": "etr-field-unit" });
  const service = createDeviceBootstrapService({
    db: database,
    factoryInstallations: ["etr-0000dd7429c2", "etr-core"]
  });

  await assert.rejects(
    service.issueFactoryTicket({ decodedUser: { uid: "field-device-uid" } }),
    error => error.status === 403 && error.code === "factory_device_refused"
  );
});
