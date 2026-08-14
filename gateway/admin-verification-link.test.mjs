import assert from "node:assert/strict";
import test from "node:test";
import express from "express";
import { installAdminVerificationLinkRoute } from "./admin-verification-link.mjs";

const NOW = Date.parse("2026-08-14T18:10:00.000Z");

async function withServer({ decoded, account, link = "https://example.test/verify?oobCode=abc" }, run) {
  const app = express();
  app.use(express.json());
  const auth = {
    async getUser(uid) {
      assert.equal(uid, decoded.uid);
      return { ...account };
    },
    async generateEmailVerificationLink(email, settings) {
      assert.equal(email, account.email);
      assert.equal(settings.handleCodeInApp, false);
      assert.match(settings.url, /oryx-froid-industriel\.web\.app\/etr-project/);
      return link;
    },
    async updateUser(uid, patch) {
      assert.equal(uid, decoded.uid);
      assert.deepEqual(patch, { emailVerified: true });
      return { ...account, emailVerified: true };
    }
  };
  installAdminVerificationLinkRoute({
    app,
    auth,
    verifyIdToken: async token => {
      assert.equal(token, "valid-token");
      return decoded;
    },
    now: () => NOW
  });
  const server = app.listen(0, "127.0.0.1");
  await new Promise(resolve => server.once("listening", resolve));
  try {
    const address = server.address();
    await run(`http://127.0.0.1:${address.port}`);
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
}

test("generates a no-store verification link only for the connected ORYX admin account", async () => {
  const decoded = {
    uid: "admin-uid",
    email: "amotard.oryx@gmail.com",
    oryxAdmin: true,
    auth_time: Math.floor(NOW / 1000) - 60
  };
  await withServer({ decoded, account: { uid: "admin-uid", email: decoded.email, emailVerified: false } }, async origin => {
    const response = await fetch(`${origin}/api/admin/self-email-verification-link`, {
      method: "POST",
      headers: { authorization: "Bearer valid-token", "content-type": "application/json" },
      body: "{}"
    });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "no-store");
    const payload = await response.json();
    assert.equal(payload.ok, true);
    assert.equal(payload.alreadyVerified, false);
    assert.equal(payload.email, decoded.email);
    assert.match(payload.verificationUrl, /oobCode=abc/);
  });
});

test("direct self verification marks only the connected ORYX admin account verified", async () => {
  const decoded = {
    uid: "admin-uid",
    email: "amotard.oryx@gmail.com",
    oryxAdmin: true,
    auth_time: Math.floor(NOW / 1000) - 45
  };
  await withServer({ decoded, account: { uid: decoded.uid, email: decoded.email, emailVerified: false } }, async origin => {
    const response = await fetch(`${origin}/api/admin/self-email-verify`, {
      method: "POST",
      headers: { authorization: "Bearer valid-token", "content-type": "application/json" },
      body: "{}"
    });
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("cache-control"), "no-store");
    const payload = await response.json();
    assert.equal(payload.ok, true);
    assert.equal(payload.verified, true);
    assert.equal(payload.email, decoded.email);
    assert.equal(payload.mode, "admin-self-verification");
  });
});

test("rejects stale authentication", async () => {
  const decoded = {
    uid: "admin-uid",
    email: "amotard.oryx@gmail.com",
    oryxAdmin: true,
    auth_time: Math.floor(NOW / 1000) - 3600
  };
  await withServer({ decoded, account: { uid: decoded.uid, email: decoded.email, emailVerified: false } }, async origin => {
    const response = await fetch(`${origin}/api/admin/self-email-verification-link`, {
      method: "POST",
      headers: { authorization: "Bearer valid-token", "content-type": "application/json" },
      body: "{}"
    });
    assert.equal(response.status, 401);
    const payload = await response.json();
    assert.equal(payload.code, "admin/recent-auth-required");
  });
});

test("returns already verified without generating a link", async () => {
  const decoded = {
    uid: "admin-uid",
    email: "amotard.oryx@gmail.com",
    oryxDeveloper: true,
    auth_time: Math.floor(NOW / 1000) - 30
  };
  let generated = false;
  const app = express();
  app.use(express.json());
  installAdminVerificationLinkRoute({
    app,
    auth: {
      async getUser() { return { uid: decoded.uid, email: decoded.email, emailVerified: true }; },
      async generateEmailVerificationLink() { generated = true; return "never"; },
      async updateUser() { throw new Error("must not update an already verified account"); }
    },
    verifyIdToken: async () => decoded,
    now: () => NOW
  });
  const server = app.listen(0, "127.0.0.1");
  await new Promise(resolve => server.once("listening", resolve));
  try {
    const address = server.address();
    const response = await fetch(`http://127.0.0.1:${address.port}/api/admin/self-email-verification-link`, {
      method: "POST",
      headers: { authorization: "Bearer valid-token", "content-type": "application/json" },
      body: "{}"
    });
    const payload = await response.json();
    assert.equal(payload.alreadyVerified, true);
    assert.equal(generated, false);
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
});
