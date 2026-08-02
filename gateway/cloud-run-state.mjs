import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

export function parseCloudRunServiceState(value) {
  const document = typeof value === "string" ? JSON.parse(value) : value;
  if (!document || typeof document !== "object" || Array.isArray(document)) {
    throw new TypeError("cloud_run_service_payload_invalid");
  }

  const status = document.status && typeof document.status === "object"
    ? document.status
    : document;
  const url = String(status.url || "").trim();
  const revision = String(status.latestReadyRevisionName || "").trim();
  const traffic = Array.isArray(status.traffic) ? status.traffic : [];

  if (!/^https:\/\/[-a-z0-9]+(?:\.[-a-z0-9]+)*$/i.test(url)) {
    throw new Error("cloud_run_service_url_missing");
  }
  if (!/^[a-z][a-z0-9-]{2,62}-[a-z0-9-]{1,63}-[a-z0-9-]{1,63}$/i.test(revision)) {
    throw new Error("cloud_run_latest_revision_missing");
  }

  const activeTraffic = traffic.find(entry => {
    if (!entry || typeof entry !== "object") return false;
    const percent = Number(entry.percent || 0);
    const targetsLatest = entry.latestRevision === true
      || String(entry.revisionName || "") === revision;
    return percent === 100 && targetsLatest;
  });
  if (!activeTraffic) {
    throw new Error("cloud_run_latest_revision_not_at_100_percent");
  }

  return {
    url,
    revision,
    trafficPercent: Number(activeTraffic.percent),
    trafficTarget: activeTraffic.latestRevision === true ? "latestRevision" : revision,
  };
}

async function main() {
  const path = process.argv[2];
  if (!path) throw new Error("cloud_run_service_file_required");
  const state = parseCloudRunServiceState(await readFile(path, "utf8"));
  process.stdout.write(`${JSON.stringify(state, null, 2)}\n`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch(error => {
    console.error(error instanceof Error ? error.message : String(error));
    process.exitCode = 1;
  });
}
