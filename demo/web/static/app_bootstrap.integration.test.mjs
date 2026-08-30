// Integration tests for demo/web/static/app.js bootstrap/retry behavior.
// Runs the REAL app.js inside a node vm with a minimal DOM/fetch emulation.
// Run: node --test demo/web/static/app_bootstrap.integration.test.mjs
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";

const appSrc = fs.readFileSync(
  path.resolve("demo/web/static/app.js"),
  "utf8"
);

function makeBtnStub() {
  return {
    tagName: "BUTTON",
    className: "",
    type: "",
    textContent: "",
    disabled: false,
    style: {},
    listeners: {},
    addEventListener(ev, fn) {
      this.listeners[ev] = fn;
    },
    click() {
      this.listeners.click?.();
    },
  };
}

/**
 * Build an isolated vm sandbox for app.js.
 * opts.tenants: array returned by /api/tenants (empty array = endpoint fails).
 * opts.manifestStatuses: per-call status list for /api/manifest; the last
 *   entry repeats for further calls. null entry = network failure.
 */
function buildSandbox({ tenants = ["tenant-a", "tenant-b"], manifestStatuses = [200] } = {}) {
  const tabsEl = {
    innerHTML: "",
    append(...nodes) {
      for (const n of nodes) this.children.push(n);
    },
    children: [],
  };
  const titleEl = { textContent: "" };
  const filterEl = { addEventListener() {}, value: "" };
  const tenantSelect = { innerHTML: "", value: "", addEventListener() {} };
  const agentSelect = { innerHTML: "", value: "", addEventListener() {} };
  const messagesEl = { innerHTML: "" };
  const statusEl = { textContent: "", style: {} };
  const els = {
    tabBar: tabsEl,
    tableTitle: titleEl,
    filter: filterEl,
    tenantSelect,
    agentSelect,
    messages: messagesEl,
    status: statusEl,
  };
  const pendingTimers = [];
  const fetchLog = [];
  let manifestCalls = 0;

  const sandbox = {
    console,
    setTimeout: (fn, ms) => {
      pendingTimers.push({ fn, ms });
      return 0;
    },
    URL: class {
      constructor(u) {
        this.href = u;
        this.searchParams = { set() {} };
      }
    },
    window: {
      location: { protocol: "http:", host: "test.local", origin: "http://test.local", search: "" },
      localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
      __agentTutorSetAgent: null,
    },
    document: {
      getElementById: (id) => els[id] ?? null,
      querySelector: (sel) =>
        ({
          "#tableTitle": titleEl,
          "#filter": filterEl,
          "#tableBody": { innerHTML: "" },
          "#metrics": { innerHTML: "" },
          "#tenantSelect": tenantSelect,
          "#status": statusEl,
          "#messages": messagesEl,
        })[sel] ?? null,
      querySelectorAll: () => [],
      createElement: (tag) => (tag === "button" ? makeBtnStub() : { style: {}, textContent: "" }),
      addEventListener() {},
    },
  };
  sandbox.fetch = async (url, opts2 = {}) => {
    fetchLog.push({ url: String(url), headers: opts2.headers || {} });
    if (String(url).includes("/api/tenants")) {
      if (tenants.length === 0) {
        return { ok: false, status: 500, json: async () => ({}) };
      }
      return { ok: true, status: 200, json: async () => ({ tenants: [...tenants] }) };
    }
    if (String(url).includes("/api/agents")) {
      return { ok: true, status: 200, json: async () => ({ agents: [] }) };
    }
    if (String(url).includes("/api/manifest")) {
      const status = manifestStatuses[Math.min(manifestCalls, manifestStatuses.length - 1)];
      manifestCalls++;
      if (status === 200) {
        return { ok: true, status, json: async () => ({ endpoints: [], entities: [] }) };
      }
      return { ok: false, status, json: async () => ({}) };
    }
    return { ok: true, status: 200, json: async () => ({}) };
  };

  const context = vm.createContext(sandbox);
  // Dynamic import() inside the vm needs importModuleDynamically (Node >= 20).
  const run = (expr) =>
    vm.runInContext(expr, context, {
      filename: "app.js",
      importModuleDynamically: async (spec) => {
        const modUrl = new URL(spec, "file://" + path.resolve("demo/web/static/") + "/").href;
        return await import(modUrl);
      },
    });
  run(appSrc);

  return {
    run,
    fetchLog,
    pendingTimers,
    tabsEl,
    titleEl,
    tenantSelect,
    fetchLog: fetchLog,
    manifestCallCount: () => fetchLog.filter((e) => e.url.includes("/api/manifest")).length,
    tenantsCallCount: () => fetchLog.filter((e) => e.url.includes("/api/tenants")).length,
  };
}

// ── Race: reloadForNewTenant mid-backoff must abort the stale cycle ────────

test("race integration: reloadForNewTenant mid-backoff aborts old cycle; new tenant renders once", async () => {
  const t = buildSandbox({ tenants: ["tenant-a", "tenant-b"], manifestStatuses: [503, 503, 200] });

  // app.js auto-runs init() at top level; wait for its bootstrap fetches.
  await new Promise((r) => setTimeout(r, 20));

  // (select change handler = setTenantId then reloadForNewTenant)
  t.run('setTenantId("tenant-b")');
  const reloadPromise = t.run("reloadForNewTenant()");

  for (let i = 0; i < 10 && t.pendingTimers.length; i++) {
    const timer = t.pendingTimers.shift();
    timer.fn();
    await new Promise((r) => setImmediate(r));
  }

  await reloadPromise;
  await new Promise((r) => setTimeout(r, 20));

  // Auto-init made the 1st tenant-a call (503); the user switched to
  // tenant-b while the stale cycle slept in backoff. The live cycle's
  // calls all carry tenant-b. The invariant: after the generation bump,
  // NO further tenant-a manifest call may appear (stale cycle died).
  const manifestTenants = t.fetchLog
    .filter((e) => e.url.includes("/api/manifest"))
    .map((e) => e.headers["X-Tenant-ID"]);
  assert.equal(
    manifestTenants.filter((x) => x === "tenant-a").length,
    1,
    `stale cycle must stop after generation bump; got ${JSON.stringify(manifestTenants)}`
  );
  assert.ok(
    manifestTenants.includes("tenant-b"),
    `live tenant-b cycle must have served the manifest; got ${JSON.stringify(manifestTenants)}`
  );
});

// ── Regression: persistent 404 with still-listed tenant must NOT recurse ────

test("persistent 404 with still-present tenant: one recovery check, no reload recursion", async () => {
  const t = buildSandbox({ tenants: ["tenant-a"], manifestStatuses: [404] });
  await new Promise((r) => setTimeout(r, 30)); // auto-init completes

  // recoverStaleTenant re-checked tenants once; the manifest was requested
  // exactly once (404). A second manifest call would mean the recovery
  // reloaded with the same tenant and recursed.
  assert.equal(t.manifestCallCount(), 1, `expected exactly 1 manifest call (no recursion), got ${t.manifestCallCount()}`);
  // 2 tenant calls = initial bootstrap list + one recovery re-check.
  assert.equal(t.tenantsCallCount(), 2, `expected initial list + 1 recovery check, got ${t.tenantsCallCount()}`);

  // Persistent tenant error is surfaced with a Retry button (no reload loop).
  const rendered = t.tabsEl.innerHTML + t.tabsEl.children.map((c) => c.textContent).join("|");
  assert.match(rendered, /Retry/);
  assert.match(rendered, /tenant-a/);
});

// ── Regression: repeated init after tenants failure must not reuse stale list ──

test("repeated init after tenants failure: state.tenants cleared, stale tenantId not confirmed", async () => {
  const t = buildSandbox({ tenants: [], manifestStatuses: [200] });

  // First init (auto-run at load): tenants list fails -> state.tenants [].
  await new Promise((r) => setTimeout(r, 30));
  assert.deepEqual(JSON.parse(await t.run("JSON.stringify(state.tenants)")), [], "state.tenants must be cleared when the list is unavailable");
  assert.equal(await t.run("state.tenantId"), null, "no tenant may remain confirmed when the list is unavailable");

  // Retry path runs init() again (same code as the Retry button).
  await t.run("init()");
  assert.deepEqual(JSON.parse(await t.run("JSON.stringify(state.tenants)")), []);
  assert.equal(t.manifestCallCount(), 0, "loadManifest must not run with an unconfirmed tenant after tenants failure");
});
