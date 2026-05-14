# Use HyPlan in MMGIS in 10 minutes

This walkthrough takes you from a clean clone of this repo to drawing
flight lines on an MMGIS map.  It assumes:

- macOS or Linux
- Docker Desktop installed and running
- Node 22+ and Python 3.10+ on your path
- A local checkout of [HyPlan](https://github.com/ryanpavlick/hyplan)
  alongside this repo (`pip install -e ./hyplan` reachable)

Three terminals are involved.  Each step says which one to use.

## 1. Lay out the workspace (~30 s)

Sibling layout, with HyPlan and MMGIS as peers of this repo:

```text
projects/
├── hyplan/                  # HyPlan library checkout
├── hyplan-mmgis-plugin/     # this repo
└── MMGIS/                   # MMGIS checkout (NASA-AMMOS/MMGIS development)
```

If you don't have MMGIS yet:

```bash
cd ~/projects
git clone -b development https://github.com/NASA-AMMOS/MMGIS.git
cd MMGIS
cp sample.env .env
# Edit .env: set SECRET (>= 24 chars), DB_NAME=mmgis, DB_USER=mmgis,
# DB_PASS=<anything>, AUTH=none, NODE_ENV=development, PORT=8888.
```

## 2. Patch MMGIS for symlink-friendly plugin dev (~2 min)

Stock MMGIS doesn't follow symlinked tool directories.  Two small,
forward-compatible patches let you keep your plugin in its own repo
and symlink it in.  See
[../.knowledge/knowledge-notes.md](../.knowledge/knowledge-notes.md)
for the full explanation.

`MMGIS/API/updateTools.js`, in the main Tools loop just after
`isDir = items[i].isDirectory();`:

```js
if (!isDir && items[i].isSymbolicLink()) {
    try {
        isDir = fs.statSync(path.join(toolsPath, items[i].name)).isDirectory();
    } catch (_) { /* dangling symlink; treat as non-dir */ }
}
```

`MMGIS/configuration/webpack.config.js`, inside the top-level
`resolve:` block:

```js
resolve: {
    symlinks: false,
    modules: [...],
    // ...
}
```

## 3. Add the hyplan-service to docker-compose.yml (~30 s)

`MMGIS/docker-compose.yml` ships with `mmgis` + `db` services.  Add a
third service that runs this plugin's FastAPI backend with HyPlan
mounted in:

```yaml
  hyplan-service:
    build:
      context: ../hyplan-mmgis-plugin/service
      dockerfile: Dockerfile
    ports:
      - 8100:8100
    environment:
      - HYPLAN_CAMPAIGNS_DIR=/data/campaigns
    volumes:
      - hyplan-data:/data/campaigns
      - ../hyplan:/hyplan:ro
    restart: on-failure

volumes:
  mmgis-db:
  hyplan-data:
```

## 4. Plug the plugin tool into MMGIS (~10 s)

Symlink the tool directory; webpack-dev-server will follow it through
the patches you just added.

```bash
ln -s "$HOME/projects/hyplan-mmgis-plugin/mmgis-tool/HyPlan" \
      "$HOME/projects/MMGIS/src/essence/Tools/HyPlan"
```

## 5. One-time MMGIS dependency install (~3 min)

**Terminal 1:**

```bash
cd ~/projects/MMGIS
npm install
cd configure && npm install && npm run build && cd ..
```

## 6. Start Postgres + the hyplan-service (~30 s)

**Terminal 1**, still in MMGIS:

```bash
docker compose up -d --build db hyplan-service
docker compose ps                # both should be "running"
curl -s http://localhost:8100/health | python -m json.tool
```

You should see `status=ok`, `service_version=0.3.0`.

## 7. Start MMGIS in dev mode (~30 s)

**Terminal 2** (this one stays running):

```bash
cd ~/projects/MMGIS
DB_HOST=localhost DB_PORT=$(docker compose port db 5432 | cut -d: -f2) npm start
```

The `DB_HOST`/`DB_PORT` overrides point at the Docker Compose Postgres
through the random host port it published.  Wait for the
"compiled successfully" message.  MMGIS API will be on
`http://localhost:8888`, the dev server on `http://localhost:8889`.

## 8. Add the HyPlan tool to a mission (~1 min)

In your browser: open
[http://localhost:8888/configure](http://localhost:8888/configure).

1. Create a mission (or open the **Reference Mission**).
2. **Tools** tab → drag the **HyPlan** tool from the available list
   into the active list.
3. In the HyPlan tool's config row, set
   **Service URL = `http://localhost:8100`**.
4. **Save**.

(With `AUTH=none` the configure UI is unprotected; for real
deployments use `AUTH=local` and create an admin via
`/api/users/first_signup`.)

## 9. Use the plugin (~2 min)

Open
[http://localhost:8889/?mission=<your-mission-name>](http://localhost:8889/).
Click the **airplane** icon in the toolbar — the HyPlan panel opens.

**Section 1 — Campaign**

- Name: anything.
- Aircraft: `NASA_GV` (or any from the dropdown).
- Sensor: `AVIRIS-NG`.
- Takeoff / Return: `KSBP` (or your nearest airport).

**Section 2 — Generate flight lines**

- Use MMGIS's **Draw** tool to draw a polygon over land or water.
- Back in HyPlan: set altitude (e.g. 7000 m), click
  **Generate Flight Lines**.
- A box of parallel lines renders on the map.

**Section 2c — Try a pattern**

- Pattern: `racetrack`.
- Click **Set Center on Map**, then click anywhere on the map.
- Click **Generate Pattern**.

**Section 2e — Move the pattern (v0.3 feature)**

- Pattern: pick the racetrack you just made.
- Operation: `Translate (m N/E)`.
- North: `5000`, East: `0`. Click **Apply**.
- The pattern shifts 5 km north on the map.

**Section 4 — Compute Plan**

- Select two or three flight lines (Shift+drag a box, or click in
  the line list).
- Click **Compute Plan**.  The routed plan renders, with a summary
  (segments, distance, time) below.

**Section 4b — Show Swaths (Coverage % readout, v0.2 feature)**

- Click **Generate Swaths**.  The status line includes
  `Coverage: XX.X%` measured against your drawn polygon.

**Section 6 — Export**

- Click **Export KML + GPX**.  Download links appear below.

## 10. Hot-reload iteration loop (after setup)

With the symlink + the two MMGIS patches in place:

- Edit `mmgis-tool/HyPlan/HyPlanTool.js` (or `helpers.js`) in this
  repo.
- MMGIS's webpack-dev-server picks up the change through the symlink
  and recompiles in ~5 s.
- Refresh the browser to see the new UI.

For backend changes:

- Edit `service/...` in this repo.
- Rebuild + restart the service container:
  `docker compose up -d --build hyplan-service` (~30 s including the
  fresh HyPlan pip install).

## Troubleshooting

**Click the HyPlan icon and nothing happens.**  Tool didn't register.
Run `grep -c HyPlan ~/projects/MMGIS/src/pre/tools.js` — should be > 0.
If 0, the `updateTools.js` patch didn't apply or didn't pick up the
symlink; check that `items[i].isSymbolicLink()` branch exists in
that file and restart MMGIS.

**Webpack "module not found" for `../../Basics/Map_/Map_`.**  The
`resolve.symlinks: false` patch in `webpack.config.js` is missing or
didn't load.  Stop MMGIS, verify the patch, restart.

**`/health` 404 / connection refused.**  hyplan-service container
isn't running.  `docker compose ps` — if missing, `docker compose up
-d --build hyplan-service` and watch the logs:
`docker compose logs -f hyplan-service`.  The first start
takes 30-60 s because `entrypoint.sh` pip-installs HyPlan into the
container at boot.

**Postgres connection refused from `npm start`.**  Your `.env` has
`DB_HOST=db`, which only resolves inside Docker's network.  Override
when running MMGIS on the host:
`DB_HOST=localhost DB_PORT=$(docker compose port db 5432 | cut -d: -f2) npm start`.

**Plugin tool icon shows but mission has the wrong service URL.**
The on-disk mission `config.json` is not the source of truth — MMGIS
stores configs in Postgres.  Edit via the Configure UI, or for a
quick patch:
`docker exec mmgis-db-1 psql -U mmgis -d mmgis -c "..."` against the
`configs` table.

## What's next

- Read [AGENTS.md](../AGENTS.md) for the architecture overview and
  critical rules.
- Read [.knowledge/conventions-and-gotchas.md](../.knowledge/conventions-and-gotchas.md)
  for code-style and lint contracts.
- Run `dev/smoke.sh` against the running service to exercise the
  API without the UI.
- Browse the interactive API docs at
  `http://localhost:8100/docs`.
