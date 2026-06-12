const PROFILE_ID_STORAGE_KEY = "stock-assistant-profile-id";
const DEFAULT_PROFILE_ID = "demo-user";

function getStorage() {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/**
 * Return the last-used profile ID for this device.
 * Falls back to a beginner-friendly default when storage is unavailable.
 */
export function getStoredProfileId() {
  const storage = getStorage();
  if (!storage) return DEFAULT_PROFILE_ID;
  const value = (storage.getItem(PROFILE_ID_STORAGE_KEY) || "").trim();
  return value || DEFAULT_PROFILE_ID;
}

/**
 * Persist the active profile ID for this device.
 */
export function setStoredProfileId(profileId) {
  const storage = getStorage();
  if (!storage) return;
  const normalized = String(profileId || "").trim() || DEFAULT_PROFILE_ID;
  storage.setItem(PROFILE_ID_STORAGE_KEY, normalized);
}

/**
 * Normalize user input to a safe profile ID value.
 */
export function normalizeProfileId(profileId) {
  return String(profileId || "").trim() || DEFAULT_PROFILE_ID;
}
