import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";
import { createLocalJWKSet, exportJWK, generateKeyPair, SignJWT } from "jose";
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
          const next = update(values.get(path) ?? null);
          if (next === undefined) return { committed: false, snapshot: { val: () => values.get(path) ?? null } };
          values.set(path, structuredClone(next));
          return { committed: true, snapshot: { val: () => structuredClone(next) } };
        }
      };
    }
  };
}

async function githubFixture(nowSeconds) {
  const { privateKey, publicKey } = await generateKeyPair("RS256");
  const jwk = await exportJWK(publicKey);
  jwk.kid = "github-test-key";
  jwk.alg = "RS256";
  jwk.use = "sig";
  const jwks = createLocalJWKSet({ keys: [jwk] });
  const token = await new SignJWT({
    repository: "ORYX-WORLD/EtR-core",
    ref: "refs/heads/main",
    run_id: "123456789",
    sha: "a".repeat(40),
    event_name: "push",
    workflow_ref: "ORYX-WORLD/EtR-core/.github/workflows/etr-deploy.yml@refs/heads/main"
  })
    .setProtectedHeader({ alg: "RS256", kid: "github-test-key" })
    .setIssuer("https://token.actions.githubusercontent.com")
    .setAudience("etr-bootstrap")
    .setSubject("repo:ORYX-WORLD/EtR-core:ref:refs/heads/main")
    .setIssuedAt(nowSeconds)
    .setExpirationTime(nowSeconds + 600)
    .sign(privateKey);
  return { jwks, token };
}

function signedRequest({ privateKey, action, serial, activationCode = "", hostname = "", rotationToken = "", timestamp, nonce }) {
  const body = JSON.stringify({ action, activationCode, hostname, rotationToken, serial });
  const payload = Buffer.from(`${timestamp}\n${nonce}\n${body}`, "utf8");
  const signature = crypto.sign(null, payload, privateKey).toString("base64url");
  return {
    body: {
      serial,
      ...(activationCode ? { activationCode } : {}),
      ...(hostname ? { hostname } : {}),
      ...(rotationToken ? { rotationToken } : {})
    },
    headers: {
      "x-etr-timestamp": timestamp,
      "x-etr-nonce": nonce,
      "x-etr-signature": signature
    }
  };
}

function currentTimestampMs() {
  return Date.now();
}

test("registers an Ed25519 device key only from the trusted GitHub main workflow", async () => {
  const timestamp = currentTimestampMs();
  const database = memoryDatabase();
  const github = await githubFixture(Math.floor(timestamp / 1000));
  const service = createDeviceBootstrapService({ db: database, now: () => timestamp, githubJwks: github.jwks });
  const { publicKey } = crypto.generateKeyPairSync("ed25519");
  const publicKeyPem = publicKey.export({ type: "spki", format: "pem" });
  const result = await service.register({
    token: github.token,
    serial: "0000ABCD1234EF56",
    installationId: "etr-abcd1234ef56",
    publicKeyPem
  });
  assert.equal(result.status, "registered");
  assert.equal(result.installationId, "etr-abcd1234ef56");
  assert.match(result.publicKeyFingerprint, /^[a-f0-9]{64}$/);
  const stored = database.values.get(`deviceBootstrap/${serialFingerprint("0000ABCD1234EF56")}`);
  assert.equal(stored.repository, "ORYX-WORLD/EtR-core");
  assert.equal(stored.workflowRunId, "123456789");
  assert.equal(stored.publicKeyFingerprint, result.publicKeyFingerprint);
});

test("verifies a signed activation request and rejects replay", async () => {
  const timestampMs = currentTimestampMs();
  const database = memoryDatabase();
  const github = await githubFixture(Math.floor(timestampMs / 1000));
  const service = createDeviceBootstrapService({ db: database, now: () => timestampMs, githubJwks: github.jwks });
  const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
  await service.register({
    token: github.token,
    serial: "0000ABCD1234EF56",
    publicKeyPem: publicKey.export({ type: "spki", format: "pem" })
  });
  const request = signedRequest({
    privateKey,
    action: "request",
    serial: "0000ABCD1234EF56",
    hostname: "etr-core",
    timestamp: String(Math.floor(timestampMs / 1000)),
    nonce: "ABCDEFGHIJKLMNOPQRSTUVWX"
  });
  const verified = await service.verifyDeviceRequest(request, "request");
  assert.equal(verified.installationId, "etr-abcd1234ef56");
  await assert.rejects(
    service.verifyDeviceRequest(request, "request"),
    error => error.code === "device_signature_replayed" && error.status === 409
  );
});

test("binds the signature to the request action and activation code", async () => {
  const timestampMs = currentTimestampMs();
  const database = memoryDatabase();
  const github = await githubFixture(Math.floor(timestampMs / 1000));
  const service = createDeviceBootstrapService({ db: database, now: () => timestampMs, githubJwks: github.jwks });
  const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
  await service.register({
    token: github.token,
    serial: "0000ABCD1234EF56",
    publicKeyPem: publicKey.export({ type: "spki", format: "pem" })
  });
  const request = signedRequest({
    privateKey,
    action: "request",
    serial: "0000ABCD1234EF56",
    hostname: "etr-core",
    timestamp: String(Math.floor(timestampMs / 1000)),
    nonce: "ZYXWVUTSRQPONMLKJIHGFEDC"
  });
  await assert.rejects(
    service.verifyDeviceRequest(request, "exchange"),
    error => error.code === "device_signature_invalid" && error.status === 401
  );

  const exchange = signedRequest({
    privateKey,
    action: "exchange",
    serial: "0000ABCD1234EF56",
    activationCode: "0".repeat(20),
    timestamp: String(Math.floor(timestampMs / 1000)),
    nonce: "0123456789ABCDEFGHIJKLMN"
  });
  await service.verifyDeviceRequest(exchange, "exchange");
  exchange.body.activationCode = "1".repeat(20);
  exchange.headers["x-etr-nonce"] = "0123456789ABCDEFGHIJKLMO";
  await assert.rejects(
    service.verifyDeviceRequest(exchange, "exchange"),
    error => error.code === "device_signature_invalid"
  );
});

test("rejects expired, unsigned and unregistered device requests", async () => {
  const timestampMs = currentTimestampMs();
  const database = memoryDatabase();
  const github = await githubFixture(Math.floor(timestampMs / 1000));
  const service = createDeviceBootstrapService({ db: database, now: () => timestampMs, githubJwks: github.jwks });
  await assert.rejects(
    service.verifyDeviceRequest({ body: { serial: "0000ABCD1234EF56" }, headers: {} }, "request"),
    error => error.code === "device_signature_missing"
  );

  const { privateKey, publicKey } = crypto.generateKeyPairSync("ed25519");
  await service.register({
    token: github.token,
    serial: "0000ABCD1234EF56",
    publicKeyPem: publicKey.export({ type: "spki", format: "pem" })
  });
  const expired = signedRequest({
    privateKey,
    action: "request",
    serial: "0000ABCD1234EF56",
    hostname: "etr-core",
    timestamp: String(Math.floor(timestampMs / 1000) - 301),
    nonce: "EXPIREDNONCEABCDEFGHIJKL"
  });
  await assert.rejects(
    service.verifyDeviceRequest(expired, "request"),
    error => error.code === "device_signature_expired"
  );
});
