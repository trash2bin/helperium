# uv index configuration — known traps (PyTorch mirror)

Context: `pyproject.toml` declares the CPU-only PyTorch index **first** with
`index-strategy = "unsafe-first-match"`, and pins `torch` / `torchvision` to it
via `[tool.uv.sources]`. Two non-obvious failure modes were observed
2026-09-10 while upgrading dependencies (audit P1.1). Read this before
touching `[tool.uv.index]` / `[tool.uv.sources]` or re-resolving the lock.

## Trap 1: `explicit = true` breaks the torch index pin

Adding `explicit = true` to the `pytorch-cpu` index (the standard uv pattern
for "extra index only for specific packages") makes `uv` ignore the
`torch = { index = "pytorch-cpu" }` source pin during resolution:

- torch silently resolves from PyPI instead of the CPU index;
- on Linux this pulls the default CUDA build **plus** `triton` and the
  `nvidia-*` packages (~2 GB of wheels) into a CPU-only setup.

Verified empirically: `uv lock --upgrade-package torch` with `explicit = true`
→ torch `2.14.0` from `pypi.org` + triton; without → `2.14.0+cpu` from
`download.pytorch.org`, triton removed.

**Do not add `explicit = true` to this index.**

## Trap 2: `unsafe-first-match` resolves shared deps from the mirror

Because the PyTorch index is listed first and `unsafe-first-match` uses the
first index carrying a package, a **full re-resolution** can pick up stale
copies of common packages hosted on the PyTorch mirror. Observed during a
`uv add --upgrade` re-resolution:

- `urllib3` `2.7.0` → `1.26.13` (16 known CVEs),
- `setuptools` `81.0.0` → `78.1.0` (4 CVEs),
- `packaging` `26.2` → `24.1`.

The lockfile (`uv.lock`) is sticky and protects day-to-day installs; the trap
fires only when the resolver re-decides versions from scratch:

- `uv add <pkg>` (full re-resolution),
- `uv lock --upgrade` (deliberate full upgrade — expected, verify after),
- deleting `uv.lock` / fresh clone without a lock.

**After any of these, run `uv audit` and diff versions** — e.g. compare
`git show HEAD:uv.lock` against the new lock. Security upgrades should be
followed by a version-diff check anyway (see audit report 2026-09-10).

## Safe procedure for dependency upgrades

1. `uv lock --upgrade-package <pkg>` per target package (keeps the rest pinned),
   or a full `uv lock --upgrade` when sweeping CVEs.
2. `uv sync`.
3. `uv audit --preview-features audit-command` — expect only known-unfixable
   advisories (chromadb at the time of writing).
4. Version-diff HEAD vs new lock: **0 downgrades** is the invariant.
5. `make ci` (unit) and `make ci-e2e` before merge.
