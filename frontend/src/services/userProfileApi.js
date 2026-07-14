import { requestJson } from "./httpClient";

export async function fetchUserProfile(userId, source = "dashboard") {
  const sourceQuery = source ? `&source=${encodeURIComponent(source)}` : "";
  return requestJson(`/user-profile?user_id=${encodeURIComponent(userId)}${sourceQuery}`, {
    timeoutMs: 12000,
    retries: 1,
  });
}

export async function updateUserProfileSettings(payload) {
  return requestJson("/user-profile/settings", {
    method: "POST",
    body: payload,
    retries: 0,
  });
}

export async function fetchUserWatchlist(userId) {
  return requestJson(`/user-watchlist?user_id=${encodeURIComponent(userId)}`, {
    timeoutMs: 12000,
    retries: 1,
  });
}

export async function addUserWatchlistTicker(payload) {
  return requestJson("/user-watchlist/add", {
    method: "POST",
    body: payload,
    retries: 0,
  });
}

export async function removeUserWatchlistTicker(payload) {
  return requestJson("/user-watchlist/remove", {
    method: "POST",
    body: payload,
    retries: 0,
  });
}

export async function fetchUserAlertSettings(userId) {
  return requestJson(`/user-alert-settings?user_id=${encodeURIComponent(userId)}`, {
    timeoutMs: 12000,
    retries: 1,
  });
}

export async function fetchUserAlertScan(userId) {
  return requestJson(`/user-alerts/scan?user_id=${encodeURIComponent(userId)}`, {
    timeoutMs: 14000,
    retries: 1,
  });
}

export async function updateUserAlertSettings(payload) {
  return requestJson("/user-alert-settings/update", {
    method: "POST",
    body: payload,
    retries: 0,
  });
}

export async function fetchDiscordLinkStatus(profileUserId) {
  return requestJson(
    `/discord-link/status?profile_user_id=${encodeURIComponent(profileUserId)}`,
    { timeoutMs: 12000, retries: 1 }
  );
}

export async function fetchDiscordReadiness() {
  return requestJson("/discord-link/readiness", { timeoutMs: 12000, retries: 1 });
}

export async function createDiscordLinkCode(profileUserId) {
  return requestJson("/discord-link/code", {
    method: "POST",
    body: { profile_user_id: profileUserId },
    retries: 0,
  });
}

export async function unlinkDiscordProfile(profileUserId) {
  return requestJson("/discord-link/unlink", {
    method: "POST",
    body: { profile_user_id: profileUserId },
    retries: 0,
  });
}
