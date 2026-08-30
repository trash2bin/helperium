// Pure bootstrap/retry decision logic for the demo web dashboard.
//
// Deliberately dependency-free and DOM-free so node --test can import it
// directly (see bootstrap_core.test.mjs). app.js consumes these functions via
// a dynamic import() from its classic <script> context.

/**
 * Retry/error taxonomy shared by app.js and the regression tests.
 * Manifest outcomes:
 *   "ok"              — manifest loaded
 *   "tenant-not-found" — 404: permanent, never retried
 *   "retryable"       — 5xx / network / transient 4xx: bounded retries
 * Tenants-list failures:
 *   "backend-unavailable" — never fabricate a tenant list, surface to user
 */

export const MAX_MANIFEST_ATTEMPTS = 3;
export const BACKOFF_BASE_MS = 1000;
export const BACKOFF_CAP_MS = 8000;

/** Advance the generation counter; every load cycle captures its own token. */
export function nextGeneration(current) {
  return (current ?? 0) + 1;
}

/** A captured generation is stale once any newer cycle has started. */
export function isStaleGeneration(captured, current) {
  return captured !== current;
}

/**
 * Render gate for a fetched manifest: only a current-generation cycle with a
 * real payload may touch the DOM. Stale cycles and empty results are dropped.
 */
export function shouldRenderManifest(captured, current, manifest) {
  if (isStaleGeneration(captured, current)) return false;
  return manifest != null;
}

/** Map a manifest fetch result (status; null = network error) to an outcome. */
export function classifyManifestStatus(status) {
  if (status === 404) return "tenant-not-found";
  return "retryable";
}

/** Map a /api/tenants failure (status; null = network error) to an outcome. */
export function classifyTenantsFailure(status) {
  return "backend-unavailable";
}

/** Bounded exponential backoff: 1s, 2s, 4s… capped at 8s. */
export function computeBackoffMs(attempt) {
  const raw = BACKOFF_BASE_MS * 2 ** Math.max(0, attempt - 1);
  return Math.min(raw, BACKOFF_CAP_MS);
}

/**
 * Choose the tenant to recover with from a freshly fetched list.
 * Returns null when the list itself is unusable (caller must surface
 * backend-unavailable, never fabricate "default").
 */
export function pickRecoveryTenant(tenants, savedTenantId) {
  if (!Array.isArray(tenants) || tenants.length === 0) return null;
  if (savedTenantId && tenants.includes(savedTenantId)) return savedTenantId;
  return tenants[0];
}

/**
 * Whether a manifest-404 recovery may switch tenant and reload the manifest.
 * Reloading with the SAME tenant would recurse (404 -> tenant re-check ->
 * same tenant -> 404 …), so reload is allowed only when the previously
 * selected tenant is genuinely gone and a different one was picked.
 */
export function shouldReloadAfterTenantCheck(recovered, previousTenantId) {
  if (!recovered) return false;
  return recovered !== previousTenantId;
}
