import assert from "node:assert/strict";
import test from "node:test";
import {
  ENROLLMENT_POLICY,
  formatActivationCode,
  generateActivationCode,
  normalizeActivationCode
} from "./enrollment.mjs";

test("uses an exact 100-bit Base32 activation policy", () => {
  assert.equal(ENROLLMENT_POLICY.activationLength, 20);
  assert.equal(ENROLLMENT_POLICY.activationBits, 100);
  const zero = generateActivationCode(size => Buffer.alloc(size, 0));
  const last = generateActivationCode(size => Buffer.alloc(size, 31));
  assert.equal(zero, "0".repeat(20));
  assert.equal(last, "Z".repeat(20));
  assert.equal(formatActivationCode(last), "ZZZZZ-ZZZZZ-ZZZZZ-ZZZZZ");
});

test("normalizes ambiguous Crockford characters from manual entry", () => {
  assert.equal(
    normalizeActivationCode("OOOOO-IIIII-LLLLL-22222"),
    "00000111111111122222"
  );
});
