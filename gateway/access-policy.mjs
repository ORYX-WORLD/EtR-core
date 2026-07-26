export function isOryxPrivileged(decoded) {
  return decoded?.oryxStaff === true || decoded?.oryxDeveloper === true;
}

export function isVerifiedHuman(decoded) {
  return decoded?.email_verified === true || decoded?.emailVerified === true;
}

export async function humanCanViewInstallation({ decoded, installationId, database }) {
  if (isOryxPrivileged(decoded)) return true;
  if (!isVerifiedHuman(decoded)) return false;
  const uid = String(decoded?.uid || decoded?.sub || "").trim();
  if (!uid || !/^[A-Za-z0-9._-]{2,80}$/.test(String(installationId || ""))) return false;
  const snapshot = await database.ref(`memberships/${uid}/${installationId}`).get();
  return snapshot.child("active").val() === true;
}
