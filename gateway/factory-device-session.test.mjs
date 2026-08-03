import assert from "node:assert/strict";
import test from "node:test";
import { createFactoryDeviceSessionIssuer } from "./factory-device-session.mjs";

function response(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    async json() { return body; }
  };
}

test("creates a self-managed factory Firebase session without Admin Auth", async () => {
  const calls = [];
  const issuer = createFactoryDeviceSessionIssuer({
    apiKey: "public-firebase-api-key-1234567890",
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), body: JSON.parse(options.body) });
      return response(200, {
        localId: "factory-device-uid",
        idToken: "header.payload.signature",
        refreshToken: "R".repeat(64),
        expiresIn: "3600"
      });
    }
  });
  const session = await issuer.issue({
    ticket: "A".repeat(43),
    serial: "0000ABCD1234EF56"
  });
  assert.equal(session.uid, "factory-device-uid");
  assert.equal(session.authMode, "factory_password_session");
  assert.equal(session.serialHash.length, 64);
  assert.equal(calls.length, 1);
  assert.match(calls[0].url, /accounts:signUp/);
  assert.match(calls[0].body.email, /^etrdev_[a-f0-9]{40}@devices\.oryx\.invalid$/);
  assert.equal(calls[0].body.returnSecureToken, true);
  assert.notEqual(calls[0].body.password, "A".repeat(43));
  assert.ok(calls[0].body.password.length >= 45);
});

test("retries idempotently with password sign-in when the factory account exists", async () => {
  const calls = [];
  const issuer = createFactoryDeviceSessionIssuer({
    apiKey: "public-firebase-api-key-1234567890",
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), body: JSON.parse(options.body) });
      if (calls.length === 1) return response(400, { error: { message: "EMAIL_EXISTS" } });
      return response(200, {
        localId: "factory-device-uid",
        idToken: "header.payload.signature",
        refreshToken: "S".repeat(64),
        expiresIn: "3600"
      });
    }
  });
  const session = await issuer.issue({ ticket: "B".repeat(43), serial: "0000ABCD1234EF56" });
  assert.equal(session.uid, "factory-device-uid");
  assert.equal(calls.length, 2);
  assert.match(calls[0].url, /accounts:signUp/);
  assert.match(calls[1].url, /accounts:signInWithPassword/);
  assert.deepEqual(calls[1].body, calls[0].body);
});

test("rejects malformed tickets before calling Firebase", async () => {
  let called = false;
  const issuer = createFactoryDeviceSessionIssuer({
    apiKey: "public-firebase-api-key-1234567890",
    fetchImpl: async () => { called = true; return response(500, {}); }
  });
  await assert.rejects(
    issuer.issue({ ticket: "short", serial: "0000ABCD1234EF56" }),
    error => error.code === "factory_ticket_invalid"
  );
  assert.equal(called, false);
});
