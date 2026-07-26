export function isVerifiedHuman(decoded) {
  return decoded?.email_verified === true || decoded?.emailVerified === true;
}

export async function clientCanViewInstallation({ decoded, db, installationId }) {
  if (!decoded?.uid) return false;
  if (decoded.oryxStaff === true || decoded.oryxDeveloper === true) return true;
  if (!isVerifiedHuman(decoded)) return false;
  const snap = await db.ref(`memberships/${decoded.uid}/${installationId}`).get();
  return snap.child("active").val() === true;
}
