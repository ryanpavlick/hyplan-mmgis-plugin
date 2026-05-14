# Knowledge Notes

Lessons learned from past sessions - gotchas not obvious from reading
the code.  Add to this file when something surprises you on a clean
clone.

## Symlinking the tool into MMGIS needs two MMGIS-side patches

The "edit in this repo, symlink into MMGIS, get HMR via
`npm start`" loop that AGENTS.md documents does **not** work against
stock MMGIS as of NASA-AMMOS/MMGIS development branch v4.3.x.  Two
patches are required on the MMGIS side; both are small,
forward-compatible, and worth proposing upstream.

### 1. `MMGIS/API/updateTools.js` — follow symlinked tool directories

`updateTools()` runs on every `npm start` and `npm run build`, scans
`src/essence/Tools/`, and writes `src/pre/tools.js` (the static tool
registry: `toolConfigs`, `toolModules`).  It checks each entry with
`Dirent.isDirectory()`, which returns **false** for symlinks pointing
at directories — so a symlinked tool gets silently skipped.  Symptom:
the tool's icon doesn't appear in the toolbar at all, and (if the
icon was hand-added to the mission config) clicking it does nothing
because the tool module isn't in `toolModules`.

Patch:

```js
// API/updateTools.js, in the main Tools loop (also applies to the
// Plugin-Tools / Private-Tools / Components loops further down)
isDir = items[i].isDirectory();
if (!isDir && items[i].isSymbolicLink()) {
    try {
        isDir = fs.statSync(path.join(toolsPath, items[i].name)).isDirectory();
    } catch (_) { /* dangling symlink; treat as non-dir */ }
}
```

### 2. `MMGIS/configuration/webpack.config.js` — `resolve.symlinks: false`

Webpack's default `resolve.symlinks: true` canonicalizes a symlinked
file to its real path **before** resolving relative imports.  For a
symlinked tool, that means `../../Basics/Map_/Map_` inside the tool's
JS resolves against the tool's **real** location (in this plugin
repo) — not against `MMGIS/src/essence/Tools/HyPlan/`.  Symptom: hot
reload triggers a fresh webpack compile that **fails** with "module
not found" for every `Basics/...` import the tool makes.

Patch:

```js
// configuration/webpack.config.js, inside the resolve: {} block
resolve: {
    symlinks: false,   // <-- add this
    modules: [...],
    ...
}
```

### Verification

After both patches, restarting `npm start`:

- `MMGIS/src/pre/tools.js` regenerates and contains the symlinked tool
  in both `toolConfigs` and `toolModules`.
- Webpack compiles without "module not found" for `../../Basics/...`
  imports.
- The tool's icon appears in the toolbar and clicks open its panel.
- Edits to `mmgis-tool/HyPlan/HyPlanTool.js` (or `helpers.js`) in this
  repo hot-reload in the browser without restarting MMGIS.

The alternative (no patches) is to **copy** the tool directory into
MMGIS instead of symlinking.  Works out of the box but kills HMR — you
re-copy + reload on every edit.

## Don't check HyPlan out into `./hyplan/` — it shadows the install

In CI we install HyPlan from a sibling checkout: `pip install -e
"./_hyplan_src[winds]"`.  The checkout target is **deliberately not**
`./hyplan` because Python's PEP 420 namespace-package finder will
treat a plain `./hyplan/` directory at the cwd as an implicit
namespace package named `hyplan`, shadowing the editable install:

```python
>>> import hyplan
>>> hyplan.__file__          # None  — namespace package, no module file
>>> dir(hyplan)              # []    — no aircraft, no FlightLine, nothing
>>> hyplan.aircraft.NASA_GV  # works — submodule import dotted-path bypasses
                             #         the shadow, but ``hyplan.NASA_GV``
                             #         doesn't exist
```

This caused every NASA_GV / NASA_ER2 test to fail in CI at the
pytest-harness commit while passing locally (no `./hyplan/` at the
local cwd).  Failure mode: `make_aircraft("NASA_GV")` →
`getattr(hyplan, "NASA_GV", None)` → None → 400 "Unknown aircraft:
'NASA_GV'".

The fix: keep the HyPlan checkout in `_hyplan_src/` (or any name
that isn't `hyplan`).  Both `release.yml` and `tests.yml` rely on
this — if you copy the workflow to a new context, preserve the
`path: _hyplan_src` checkout target.

## HyPlan `__version__` can be absent on a fresh install

setuptools-scm writes `hyplan/_version.py` at build / install time.
On a fresh `actions/checkout` of `ryanpavlick/hyplan` followed by
`pip install -e ./hyplan`, the version file occasionally fails to
materialize and `hyplan.__version__` raises `AttributeError`.

Always access via `getattr(hyplan, "__version__", "unknown")` rather
than `hyplan.__version__`.  Fixed in `service/routers/metadata.py`
after the v0.1.0 CI surfaced the issue.

## `GITHUB_TOKEN` does not trigger derivative `push: tags:` workflows

At v0.1.0 we had a separate `post-release.yml` workflow gated on
`push: tags:`.  It never fired because the tag was created and pushed
by `release.yml` using `GITHUB_TOKEN`, and GitHub Actions intentionally
suppresses derivative workflows triggered by `GITHUB_TOKEN` (loop
prevention).

Two ways to fix:

1. Push the tag with a Personal Access Token instead of
   `GITHUB_TOKEN`.
2. **Inline the post-release work into the same job that creates the
   tag.**  (Adopted in v0.2.0 - `release.yml` now bumps
   `CITATION.cff`, regenerates `SECURITY.md`, commits, tags, and
   creates the GitHub Release all in one step.  `post-release.yml`
   deleted.)

**Force-pushing an existing tag** *does* trigger workflows - we saw
this when the v0.1.0 tag was moved during the Claude-coauthor history
rewrite and `post-release.yml` fired on the move (before it was
deleted).  Useful to know for "I need this workflow to run once more"
scenarios.

## vfrmap.com AIRAC cycle scrape (28-day TTL)

`service/routers/tiles.py` proxies FAA aeronautical charts from
vfrmap.com.  vfrmap encodes the current AIRAC cycle (e.g.
`"20260319"`) in its tile URL path, and the cycle rolls every 28
days.

We scrape the cycle from `https://vfrmap.com/js/map.js` (regex
`f\s*=\s*['"](\d{8})['"]`) and cache it for an hour.  If the scrape
fails and we have no cached cycle, `/faa-tile/{kind}/{z}/{y}/{x}`
returns 503.

If vfrmap restructures their frontend the regex breaks silently
(falls back to cached value, then 503 after TTL).  Sanity-check by
hitting `/faa-tile/vfrc/5/12/13` after any reported FAA chart
breakage.

## Service version is hardcoded in `schemas.py`

`HealthResponse.service_version` has a default value baked into
`service/schemas.py`.  When releasing, bump it manually before
running `release.yml` so `/health` reports the right version
post-release.

This is a TODO to drive from `service.app.app.version` or a single
constant; for now the convention is "bump it in the PR that closes
out the unreleased CHANGELOG section."

## HyPlan dependency tracks `main`, not a release tag

CI installs HyPlan from `ryanpavlick/hyplan@main` (latest), not a
pinned release.  This means:

- An API change in HyPlan main can break this repo's CI before any
  HyPlan release ships.
- When CI fails after a green local run, check the most recent
  hyplan commits first - it may be that HyPlan main moved and broke
  us.
- We use HyPlan v1.7-staging APIs (e.g. `GlintArc.footprint`,
  `Pattern.regenerate`) that exist on main but aren't on the v1.6.3
  tag.

If we ever pin a release tag, also update `CITATION.cff` and the
README + `AGENTS.md` quick start, since they currently say "editable
HyPlan checkout."

## Service is a package, not a flat module

At v0.2.0 `service/app.py` was split into a package (state, errors,
schemas, routers/*).  The Docker invocation became
`uvicorn service.app:app` (not `uvicorn app:app`), and the Dockerfile
COPYs the whole `service/` tree (not just `app.py`).

If you add a new top-level module under `service/` it needs to be
COPY'd into the Docker image too.  See `service/Dockerfile`.

## PRs typically touch both halves

The service and frontend are decoupled by HTTP / JSON, but features
usually require both.  Examples:

- Adding a new pattern type: service handler in
  `routers/patterns.py` + UI dropdown option + parameter form +
  rendering hook in `HyPlanTool.js`.
- Changing the error response shape: service `_classify` + frontend
  `getErrorMessage`.

A service-only PR with no UI is fine as a stepping stone (the new
endpoint is curl-testable); just call out that the UI side is
deferred.

## Plans live under `plans/` (gitignored)

The roadmap and ad-hoc design notes go in `plans/` at the repo root,
which is gitignored.  These are working-copy-only and not shared via
git.  Open `plans/roadmap.md` for the current release plan.

This is the spec-kit substitute for our scale - we're not running a
full `/speckit.specify` -> `plan` -> `tasks` workflow like MMGIS
does; the project is small enough that a single roadmap file plus
CHANGELOG entries are sufficient.

## Frontend dev loop: symlink into MMGIS, use their HMR

The frontend is bundled by an MMGIS checkout's own Webpack pipeline;
there is no standalone build for the plugin.  But you do **not** need
to re-run `npm run build` every time you edit `HyPlanTool.js`.
MMGIS's `npm start` runs webpack-dev-server with HMR on `PORT+1`
(default 8889).  Symlink the tool directory once and the file
watcher picks up edits in place:

```bash
ln -s "$PWD/mmgis-tool/HyPlan" /path/to/MMGIS/src/essence/Tools/HyPlan
cd /path/to/MMGIS
npm start                                 # dev: http://localhost:8889
```

Editing `mmgis-tool/HyPlan/HyPlanTool.js` in this repo hot-reloads
inside MMGIS.  The slow `cp -r ... && npm run build` dance is only
for a one-shot install into a production MMGIS checkout that you
don't intend to keep editing.

If you don't have an MMGIS checkout at all, our CI's `frontend` job
runs ESLint but does not exercise the tool in a browser - frontend
regressions can only be caught by running it inside an MMGIS
instance.
