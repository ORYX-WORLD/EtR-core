import assert from "node:assert/strict";
import test from "node:test";

import { parseCloudRunServiceState } from "./cloud-run-state.mjs";

const service = overrides => ({
  metadata: { name: "etr-remote-gateway" },
  status: {
    url: "https://etr-remote-gateway-7n72m5gopq-ew.a.run.app",
    latestReadyRevisionName: "etr-remote-gateway-00042-abc",
    traffic: [{ latestRevision: true, percent: 100 }],
    ...overrides,
  },
});

test("accepts the full gcloud service JSON with 100 percent on latest", () => {
  assert.deepEqual(parseCloudRunServiceState(service()), {
    url: "https://etr-remote-gateway-7n72m5gopq-ew.a.run.app",
    revision: "etr-remote-gateway-00042-abc",
    trafficPercent: 100,
    trafficTarget: "latestRevision",
  });
});

test("accepts an explicit revision target matching latestReadyRevisionName", () => {
  const state = parseCloudRunServiceState(service({
    traffic: [{ revisionName: "etr-remote-gateway-00042-abc", percent: 100 }],
  }));
  assert.equal(state.trafficTarget, "etr-remote-gateway-00042-abc");
});

test("rejects a stale traffic split even when a latest revision exists", () => {
  assert.throws(
    () => parseCloudRunServiceState(service({
      traffic: [
        { revisionName: "etr-remote-gateway-00041-old", percent: 100 },
        { latestRevision: true, percent: 0 },
      ],
    })),
    /cloud_run_latest_revision_not_at_100_percent/,
  );
});

test("rejects the projected JSON wrapper that caused the deployment failure", () => {
  assert.throws(
    () => parseCloudRunServiceState({ "status.traffic": [{ latestRevision: true, percent: 100 }] }),
    /cloud_run_service_url_missing/,
  );
});

test("rejects missing URL or latest ready revision", () => {
  assert.throws(
    () => parseCloudRunServiceState(service({ url: "" })),
    /cloud_run_service_url_missing/,
  );
  assert.throws(
    () => parseCloudRunServiceState(service({ latestReadyRevisionName: "" })),
    /cloud_run_latest_revision_missing/,
  );
});
