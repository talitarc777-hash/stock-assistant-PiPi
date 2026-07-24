function normalizeApiBaseUrl(value) {
  const raw = (value || "").trim();
  if (!raw) {
    return inferApiBaseUrlFromLocation();
  }

  const withoutTrailingSlash = raw.replace(/\/+$/, "");
  if (withoutTrailingSlash.startsWith("http://") || withoutTrailingSlash.startsWith("https://")) {
    return withoutTrailingSlash;
  }

  // Vercel env vars are sometimes set as "my-app.up.railway.app" without a scheme.
  // Browsers treat that as a relative path and requests end up hitting Vercel (404).
  if (withoutTrailingSlash.startsWith("localhost") || withoutTrailingSlash.startsWith("127.0.0.1")) {
    return `http://${withoutTrailingSlash}`;
  }
  return `https://${withoutTrailingSlash}`;
}

function inferApiBaseUrlFromLocation() {
  const localFallback = "http://127.0.0.1:8000";
  const tailscaleCustomDomainFallback = "https://cowbox.dpdns.org";
  const tailnetDomain = "tail8919df.ts.net";
  if (typeof window === "undefined") return localFallback;

  const { hostname, protocol } = window.location;
  const isLocalhost = hostname === "localhost" || hostname === "127.0.0.1";
  if (isLocalhost) return localFallback;

  // When the frontend is opened through the Tailscale hostname, the API is
  // served by the same Tailscale endpoint rather than an api.* subdomain.
  if (hostname === tailnetDomain || hostname.endsWith(`.${tailnetDomain}`)) {
    return `${protocol === "http:" ? "http" : "https"}://${hostname}`;
  }

  if (hostname === "cowbox.dpdns.org") {
    return tailscaleCustomDomainFallback;
  }

  if (hostname.endsWith(".pages.dev")) {
    // Cloudflare Pages may still host the static frontend, but the API is now
    // reached through the Tailscale-backed custom domain.
    return tailscaleCustomDomainFallback;
  }

  const withoutWww = hostname.startsWith("www.") ? hostname.slice(4) : hostname;
  return `${protocol === "http:" ? "http" : "https"}://api.${withoutWww}`;
}

const API_BASE_URL = normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL);

const RETRYABLE_STATUS_CODES = new Set([408, 429, 500, 502, 503, 504]);

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function parseErrorMessage(response, payloadDetail, fallbackMessage) {
  if (response.status === 404) {
    return "Backend endpoint not found (404). Check VITE_API_BASE_URL points to the backend root (no trailing /api) and that the backend is deployed.";
  }
  if (payloadDetail) return String(payloadDetail);
  if (response.status === 503 || response.status === 502 || response.status === 504) {
    return "The server is starting up or temporarily busy. Please try again in a moment.";
  }
  if (response.status === 429) {
    return "The server is handling too many requests right now. Please try again shortly.";
  }
  return fallbackMessage;
}

export async function requestJson(path, options = {}) {
  const {
    method = "GET",
    body,
    headers = {},
    // Railway free tier can cold-start; give GETs a bit more room before surfacing "can't reach backend".
    timeoutMs = 25000,
    retries = method === "GET" ? 2 : 0,
    retryDelayMs = 650,
    signal: externalSignal = null,
  } = options;
  const url = `${API_BASE_URL}${path}`;
  if (!API_BASE_URL) {
    throw new Error(
      "Backend URL is not configured. Set VITE_API_BASE_URL to https://nanopi-r76s.tail8919df.ts.net."
    );
  }

  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort("timeout"), timeoutMs);
    let shouldRetry = false;
    try {
      if (externalSignal) {
        if (externalSignal.aborted) {
          throw new Error("Request canceled.");
        }
        externalSignal.addEventListener("abort", () => controller.abort("external_abort"), { once: true });
      }
      const response = await fetch(url, {
        method,
        headers: {
          "Content-Type": "application/json",
          ...headers,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) {
        const fallbackMessage = `Request failed with status ${response.status}`;
        let detail = "";
        try {
          const payload = await response.json();
          detail = payload?.detail || "";
        } catch {
          detail = "";
        }
        const message = parseErrorMessage(response, detail, fallbackMessage);
        shouldRetry = method === "GET" && RETRYABLE_STATUS_CODES.has(response.status) && attempt < retries;
        if (!shouldRetry) {
          throw new Error(message);
        }
      } else {
        return response.json();
      }
    } catch (error) {
      const abortedByTimeout = error?.name === "AbortError" || String(error?.message || "").includes("timeout");
      shouldRetry = method === "GET" && attempt < retries;
      if (!shouldRetry) {
        if (abortedByTimeout) {
          throw new Error(
            "The request took too long. The server may be busy or waking up, so please try again."
          );
        }
        if (
          error instanceof TypeError ||
          String(error?.message || "").toLowerCase().includes("failed to fetch")
        ) {
          throw new Error(
            "We could not reach the backend. It may be restarting, waking up, or temporarily unavailable."
          );
        }
        throw error;
      }
    } finally {
      window.clearTimeout(timeoutId);
    }
    await sleep(retryDelayMs * (attempt + 1));
  }

  throw new Error("The request did not complete. Please try again.");
}
