import assert from "node:assert/strict";
import test from "node:test";
import {
  createDeviceConnectionAuthorizer,
  readDeviceBinding
} from "./device-authorization.mjs";

function response(status, value) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return value; }
  };
}

test("reads the installation bound to the technical UID with the device token", async () => {
  const requests = [];
  const value = await readDeviceBinding({
    databaseURL: "https://example-default-rtdb.europe-west1.firebasedatabase.app",
    uid: "technical-uid",
    token: "header.payload.signature",
    fetchImpl: async (url, options) => {
      requests.push({ url: String(url), options });
      return response(200, "etr-core");
    }
  });
  assert.equal(value, "etr-core");
  assert.equal(requests.length, 1);
  const url = new URL(requests[0].url);
  assert.equal(url.pathname, "/deviceAccess/technical-uid.json");
  assert.equal(url.searchParams.get("auth"), "header.payload.signature");
});

test("authorizes only the installation stored in deviceAccess", async () => {
  const authorize = createDeviceConnectionAuthorizer({
    databaseURL: "https://example.firebasedatabase.app",
    verifyIdToken: async () => ({ uid: "technical-uid" }),
    fetchImpl: async () => response(200, "etr-core")
  });
  const allowed = await authorize({
    token: "header.payload.signature",
    installationId: "etr-core"
  });
  assert.equal(allowed.allowed, true);
  assert.equal(allowed.linkedInstallationId, "etr-core");

  const refused = await authorize({
    token: "header.payload.signature",
    installationId: "etr-0000dd7429c2"
  });
  assert.equal(refused.allowed, false);
});

test("maps an expired or refused Firebase session to an authentication error", async () => {
  await assert.rejects(
    readDeviceBinding({
      databaseURL: "https://example.firebasedatabase.app",
      uid: "technical-uid",
      token: "expired",
      fetchImpl: async () => response(401, { error: "Permission denied" })
    }),
    (error) => {
      assert.equal(error.status, 401);
      assert.equal(error.code, "device-access/firebase-401");
      return true;
    }
  );
});

test("maps transport failures to a temporary gateway error", async () => {
  await assert.rejects(
    readDeviceBinding({
      databaseURL: "https://example.firebasedatabase.app",
      uid: "technical-uid",
      token: "token",
      fetchImpl: async () => { throw new Error("network"); }
    }),
    (error) => {
      assert.equal(error.status, 503);
      assert.equal(error.code, "device-access/network-error");
      return true;
    }
  );
});

test("rejects an invalid deviceAccess value", async () => {
  await assert.rejects(
    readDeviceBinding({
      databaseURL: "https://example.firebasedatabase.app",
      uid: "technical-uid",
      token: "token",
      fetchImpl: async () => response(200, { installation: "etr-core" })
    }),
    (error) => {
      assert.equal(error.status, 503);
      assert.equal(error.code, "device-access/invalid-binding");
      return true;
    }
  );
});
