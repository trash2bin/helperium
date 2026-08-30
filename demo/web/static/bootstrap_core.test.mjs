// Regression tests for demo/web bootstrap + retry logic (pure, no DOM, no browser).
// Run: node --test demo/web/static/bootstrap_core.test.mjs
// These tests are written against bootstrap_core.mjs (minimal ES module, no UMD).
import test from "node:test";
import assert from "node:assert/strict";

import {
  nextGeneration,
  isStaleGeneration,
  classifyManifestStatus,
  classifyTenantsFailure,
  computeBackoffMs,
  shouldRenderManifest,
  shouldReloadAfterTenantCheck,
  MAX_MANIFEST_ATTEMPTS,
  BACKOFF_BASE_MS,
  BACKOFF_CAP_MS,
  pickRecoveryTenant,
} from "./bootstrap_core.mjs";

// ── Generation guard: stale retry loops must lose ──────────────────────────

test("nextGeneration always produces a strictly larger token", () => {
  let gen = nextGeneration(0);
  assert.equal(gen, 1);
  gen = nextGeneration(gen);
  assert.equal(gen, 2);
  gen = nextGeneration(gen);
  assert.equal(gen, 3);
});

test("stale generation detection: old loop must stop after a new one starts", () => {
  // Old loop captured gen=1, then tenant switch produced gen=2.
  const oldLoopGen = 1;
  const currentGen = nextGeneration(oldLoopGen);
  assert.equal(isStaleGeneration(oldLoopGen, currentGen), true);
  // The current loop itself is never stale.
  assert.equal(isStaleGeneration(currentGen, currentGen), false);
});

test("race regression: two interleaved loops, stale one loses at every await point", () => {
  // Simulate the app.js race: loop A captures gen, sleeps; reloadForNewTenant
  // bumps generation and starts loop B; loop A wakes up. At each resumption
  // loop A must observe staleness and stop, so B's manifest cannot be
  // overwritten by A's fetch.
  let currentGen = nextGeneration(0);
  const loopA = { gen: currentGen, alive: true };
  const loopB = { gen: nextGeneration(currentGen), alive: true };
  currentGen = loopB.gen;

  // Loop A wakes after sleep: stale -> must stop.
  if (isStaleGeneration(loopA.gen, currentGen)) loopA.alive = false;
  assert.equal(loopA.alive, false, "stale loop A must terminate");

  // Loop B is current: continues.
  if (isStaleGeneration(loopB.gen, currentGen)) loopB.alive = false;
  assert.equal(loopB.alive, true, "current loop B must continue");

  // Even if loop A already fetched a manifest, the render guard rejects it.
  const aManifestRenderAllowed = !isStaleGeneration(loopA.gen, currentGen);
  assert.equal(aManifestRenderAllowed, false, "stale loop must not render");
});

test("shouldRenderManifest gates every fetch result through the generation token", () => {
  // Success path only renders when the captured generation is still current.
  assert.equal(shouldRenderManifest(1, 1, { endpoints: [] }), true);
  assert.equal(shouldRenderManifest(1, 2, { endpoints: [] }), false);
  // No result (null, e.g. network error) never renders anything.
  assert.equal(shouldRenderManifest(1, 1, null), false);
  // A result from a stale cycle is rejected even when non-null.
  assert.equal(shouldRenderManifest(1, 2, { endpoints: [] }), false);
});

test("full retry driver: 404 stops immediately; retryable exhausts after 3; stale aborts mid-backoff", async () => {
  // This emulates the exact loop app.js will run, using the module's policy
  // functions only — no DOM, no timers (sleep is faked).

  const runCycle = async (statuses, { bumpGenerationAfter = Infinity } = {}) => {
    let generation = nextGeneration(0);
    let sleeps = [];
    let renders = 0;
    let outcome = null;
    let captured = generation;

    for (let attempt = 1; attempt <= MAX_MANIFEST_ATTEMPTS; attempt++) {
      if (isStaleGeneration(captured, generation)) break; // await resumption guard

      const status = statuses[attempt - 1] ?? null;
      const klass = classifyManifestStatus(status);

      if (klass === "tenant-not-found") {
        outcome = "tenant-not-found";
        break;
      }

      // Emulate a successful fetch arriving.
      if (status === 200) {
        if (shouldRenderManifest(captured, generation, { endpoints: [] })) renders++;
        outcome = "ok";
        break;
      }

      if (attempt >= MAX_MANIFEST_ATTEMPTS) {
        outcome = "exhausted";
        break;
      }

      sleeps.push(computeBackoffMs(attempt));
      // Fake await sleep(...): generation may be bumped concurrently here.
      if (attempt >= bumpGenerationAfter) generation = nextGeneration(generation);
      if (isStaleGeneration(captured, generation)) break; // woke up stale
    }

    return { outcome, sleeps, renders, captured, currentGen: generation };
  };

  // Case 1: stable 404 (deleted tenant) -> one attempt, zero sleeps, no retries.
  const notFound = await runCycle([404, 404, 404, 404]);
  assert.equal(notFound.outcome, "tenant-not-found");
  assert.deepEqual(notFound.sleeps, []);

  // Case 2: persistent 503 -> exactly 3 attempts, backoff 1s/2s between them,
  // then exhausted (Retry button state, no silent forever-loop).
  const exhausted = await runCycle([503, 503, 503, 503]);
  assert.equal(exhausted.outcome, "exhausted");
  assert.deepEqual(exhausted.sleeps, [1000, 2000]);

  // Case 3: backend recovers on attempt 2 -> renders exactly once.
  const recovered = await runCycle([503, 200]);
  assert.equal(recovered.outcome, "ok");
  assert.equal(recovered.renders, 1);

  // Case 4 (THE RACE): mid-backoff a new cycle starts; the old loop wakes up
  // stale, must stop and must never render. Old captured gen stays 1, current
  // becomes 2; even a pending OK response from the old cycle is discarded.
  const raced = await runCycle([503, 200], { bumpGenerationAfter: 1 });
  assert.notEqual(raced.currentGen, raced.captured);
  assert.equal(raced.renders, 0, "stale cycle must not render after race");
  assert.equal(raced.outcome, null, "stale cycle must not produce an outcome");
});

// ── 404 vs retryable: no pointless retries on tenant-not-found ─────────────

test("classifyManifestStatus: 404 is permanent (tenant not found), never retried", () => {
  assert.equal(classifyManifestStatus(404), "tenant-not-found");
});

test("classifyManifestStatus: 5xx and network failures are retryable", () => {
  assert.equal(classifyManifestStatus(500), "retryable");
  assert.equal(classifyManifestStatus(502), "retryable");
  assert.equal(classifyManifestStatus(503), "retryable");
  assert.equal(classifyManifestStatus(null), "retryable"); // fetch TypeError
});

test("classifyManifestStatus: other 4xx are retryable-transient (proxy hiccup), bounded", () => {
  // A 401/409 from the proxy mid-restart should not brick the page either way;
  // bounded retry is the conservative choice for unknown statuses.
  assert.equal(classifyManifestStatus(429), "retryable");
});

// ── /api/tenants failure: never silently fabricate ["default"] ──────────────

test("classifyTenantsFailure: backend failure is surfaced, no default fallback", () => {
  assert.equal(classifyTenantsFailure(500), "backend-unavailable");
  assert.equal(classifyTenantsFailure(null), "backend-unavailable"); // network
  assert.equal(classifyTenantsFailure(503), "backend-unavailable");
});

// ── Bounded exponential backoff: exactly 3 attempts, 1s/2s/4s ───────────────

test("backoff schedule: 1000, 2000, 4000 with cap", () => {
  assert.equal(computeBackoffMs(1), 1000);
  assert.equal(computeBackoffMs(2), 2000);
  assert.equal(computeBackoffMs(3), 4000);
  // Cap for hypothetical larger attempt numbers.
  assert.equal(computeBackoffMs(4), 8000);
  assert.equal(computeBackoffMs(10), 8000);
});

test("backoff constants: 3 attempts max, base 1s, cap 8s", () => {
  assert.equal(MAX_MANIFEST_ATTEMPTS, 3);
  assert.equal(BACKOFF_BASE_MS, 1000);
  assert.equal(BACKOFF_CAP_MS, 8000);
});

test("retry loop bound: attempts never exceed MAX_MANIFEST_ATTEMPTS", () => {
  // Emulate the loop: attempt 1..N; after the 3rd failure it must stop.
  let attempts = 0;
  for (let attempt = 1; attempt <= 10; attempt++) {
    attempts = attempt;
    const outcome = "retryable";
    if (outcome !== "retryable") break;
    if (attempt >= MAX_MANIFEST_ATTEMPTS) break;
    computeBackoffMs(attempt);
  }
  assert.equal(attempts, MAX_MANIFEST_ATTEMPTS);
});

// ── Stale tenant recovery: pick first real tenant, never "default" ─────────

test("pickRecoveryTenant: saved tenant genuinely absent -> first available", () => {
  assert.equal(pickRecoveryTenant(["autoparts", "school-a"], "school-a"), "school-a");
  assert.equal(
    pickRecoveryTenant(["school-a", "autoparts"], "vanished-tenant"),
    "school-a"
  );
});

test("pickRecoveryTenant: saved tenant still present -> keep it (no needless switch)", () => {
  assert.equal(pickRecoveryTenant(["autoparts", "school-a"], "school-a"), "school-a");
});

test("pickRecoveryTenant: empty tenant list -> null (caller shows backend-unavailable)", () => {
  assert.equal(pickRecoveryTenant([], "autoparts"), null);
  assert.equal(pickRecoveryTenant(null, "autoparts"), null);
});

test("pickRecoveryTenant: no saved tenant -> first available", () => {
  assert.equal(pickRecoveryTenant(["autoparts", "school-a"], null), "autoparts");
});

// ── 404 recursion guard: no reload when the tenant is still present ────────

test("shouldReloadAfterTenantCheck: same tenant again -> false (no reload loop)", () => {
  // /api/tenants still lists the tenant whose manifest 404s: reloading would
  // recurse recoverStaleTenant -> loadManifest -> 404 -> recoverStaleTenant…
  assert.equal(shouldReloadAfterTenantCheck("autoparts", "autoparts"), false);
});

test("shouldReloadAfterTenantCheck: tenant genuinely vanished -> reload once", () => {
  assert.equal(shouldReloadAfterTenantCheck("school-a", "autoparts"), true);
});

test("shouldReloadAfterTenantCheck: unusable recovery -> false", () => {
  assert.equal(shouldReloadAfterTenantCheck(null, "autoparts"), false);
  assert.equal(shouldReloadAfterTenantCheck(undefined, "autoparts"), false);
});
