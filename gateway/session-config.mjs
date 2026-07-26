import { createFirebasePasswordSessionIssuer } from "./device-session.mjs";

export function createDefaultDeviceSessionIssuer(auth) {
  return createFirebasePasswordSessionIssuer({
    auth,
    apiKey: process.env.FIREBASE_WEB_API_KEY || process.env.FIREBASE_API_KEY
  });
}
