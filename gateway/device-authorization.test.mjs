import assert from "node:assert/strict";
import test from "node:test";
import {
  createDeviceConnectionAuthorizer,
  decodeFirebaseDeviceIdentity,
  readDeviceBinding
} from "./device-authorization.mjs";

const PROJECT_ID = "oryx-froid-industriel";

function response(status, value) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return value; }
  };
}

function encode(value) {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

function firebaseToken({
  uid = "technical-uid",
  projectId = PROJECT_ID,
  now = Math.floor(Date.now() / 1000),
  exp = now + 3600,
  alg = "RS256"
} = {}) {
  return `${encode({ alg, kid: "test-key" })}.${encode({
    aud: projectId,
    iss: `https://securetoken.google.com/${projectId}`,
    sub: uid,
    user_id: uid,
    iat: now - 30,
    auth_time: now - 60,
    exp
  })}.test-signature`;
}

test("decodes only a structurally valid Firebase device identity", () => {
  const now = 2_000_000_000;
  const token = firebaseToken({ now });
  const decoded = decodeFirebaseDeviceIdentity(token, {
    projectId: PROJECT_ID,
    now
  });
  assert.equal(decoded.uid, "technical-uid");
  assert.equal(decoded.aud, PROJECT_ID);

  assert.throws(
    () => decodeFirebaseDeviceIdentity(firebaseToken({ now, exp: now - 10 }), {
      projectId: PROJECT_ID,
      now
    }),
    (error) => error.status === 401 && error.code === "device-access/token-expired"
  );
});

test("reads the installation bound to the technical UID with the device token", async () => {
  const requests = [];
  const token = firebaseToken();
  const value = await readDeviceBinding({
    databaseURL: "https://example-default-rtdb.europe-west1.firebasedatabase.app",
    uid: "technical-uid",
    token,
    fetchImpl: async (url, options) => {
      requests.push({ url: String(url), options });
      return response(200, "etr-core");
    }
  });
  assert.equal(value, "etr-core");
  assert.equal(requests.length, 1);
  const url = new URL(requests[0].url);
  assert.equal(url.pathname, "/deviceAccess/technical-uid.json");
  assert.equal(url.searchParams.get("auth"), token);
});

test("authorizes only the installation stored in deviceAccess without Firebase Admin lookup", async () => {
  const authorize = createDeviceConnectionAuthorizer({
    projectId: PROJECT_ID,
    databaseURL: "https://example.firebasedatabase.app",
    fetchImpl: async () => response(200, "etr-core")
  });
  const token = firebaseToken();
  const allowed = await authorize({
    token,
    installationId: "etr-core"
  });
  assert.equal(allowed.allowed, true);
  assert.equal(allowed.linkedInstallationId, "etr-core");
  assert.equal(allowed.decoded.uid, "technical-uid");

  const refused = await authorize({
    token,
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
