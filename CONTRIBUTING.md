# Contributing to hyplan-mmgis-plugin

Thank you for your interest in contributing! This guide will help you
get started.

The repository has two components that often move together:

- `service/` — the FastAPI Python backend that wraps
  [HyPlan](https://github.com/ryanpavlick/hyplan)
- `mmgis-tool/HyPlan/` — the browser-side
  [MMGIS](https://github.com/NASA-AMMOS/MMGIS) tool plugin (JavaScript + CSS)

A change to one usually affects the other. PRs that touch both are normal.

## Getting Help

Before opening an issue, skim the [README](README.md) and
[`docs/`](docs/) — most "how do I do X" answers live there.

If that doesn't cover your question:

- **General usage questions** — open a
  [GitHub issue](https://github.com/ryanpavlick/hyplan-mmgis-plugin/issues/new)
  and apply the `question` label.
- **Bug reports** — see [Reporting Bugs](#reporting-bugs) below.
- **Feature requests** — see [Feature Requests](#feature-requests) below.
- **Security disclosures** — follow the process in [SECURITY.md](SECURITY.md).
- **Direct contact** — for things that don't fit the above (collaboration
  inquiries, anything sensitive), email Ryan Pavlick at
  <ryan.p.pavlick@nasa.gov>.

We aim to respond to issues within a few working days; this is a small
project so please be patient.

## Getting Started

1. **Fork** the repository on GitHub.
2. **Clone** your fork locally:
   ```bash
   git clone https://github.com/<your-username>/hyplan-mmgis-plugin.git
   cd hyplan-mmgis-plugin
   ```
3. **Install** the service in development mode:
   ```bash
   pip install -e ../hyplan       # editable HyPlan checkout
   pip install -r service/requirements.txt
   ```
4. **Create a branch** for your changes:
   ```bash
   git checkout -b my-feature
   ```

## Development Workflow

### Running the service locally

```bash
HYPLAN_CAMPAIGNS_DIR=/tmp/hyplan-campaigns \
  uvicorn service.app:app --reload --port 8100
```

Smoke-test the running service:

```bash
curl http://127.0.0.1:8100/health
```

### Running the frontend in MMGIS

The plugin is a normal MMGIS tool. After editing `HyPlanTool.js` or
`HyPlanTool.css`, copy the tool directory into your MMGIS source tree
and rebuild MMGIS:

```bash
cp -r mmgis-tool/HyPlan /path/to/MMGIS/src/essence/Tools/HyPlan
cd /path/to/MMGIS
npm run build      # or your local MMGIS dev command
```

### Code style

- **Python** (`service/`): follow [PEP 8](https://peps.python.org/pep-0008/)
  conventions. CI runs `ruff check service/`.
- **JavaScript** (`mmgis-tool/HyPlan/`): match the style of the surrounding
  MMGIS code (4-space indent, single quotes, no semicolons on statement
  ends — the existing file is the reference). The plugin keeps map/UI
  state in-module and delegates planning work to the service; please
  preserve that split. CI runs `npm run lint` (ESLint flat config with
  `@eslint/js` recommended rules) — errors block the build, warnings do
  not.
- Public service endpoints should keep stable request/response shapes
  across patch releases; add new fields rather than renaming existing ones.

### Documentation

- Update [README.md](README.md) when adding or removing endpoints or
  UI sections.
- Add or update files in [`docs/`](docs/) for non-trivial features.

## Submitting Changes

1. Run the service locally and exercise the affected endpoints with
   `curl` or the MMGIS UI.
2. Make sure `ruff check service/` is clean.
3. Update [CHANGELOG.md](CHANGELOG.md) under the appropriate
   unreleased section.
4. Commit your changes with a clear, descriptive message.
5. Push to your fork and open a **pull request** against `main`.
6. Describe what your PR does and why.

## Reporting Bugs

Open an
[issue](https://github.com/ryanpavlick/hyplan-mmgis-plugin/issues) with:

- A clear title and description.
- Steps to reproduce the problem.
- Expected vs. actual behavior.
- Component(s) affected: service, frontend, or both.
- HyPlan version (`python -c "import hyplan; print(hyplan.__version__)"`),
  Python version, MMGIS version, and OS.

## Feature Requests

Feature requests are welcome! Please open an
[issue](https://github.com/ryanpavlick/hyplan-mmgis-plugin/issues)
describing the use case and proposed behavior.

## License

By contributing, you agree that your contributions will be licensed
under the [Apache License 2.0](LICENSE.md).
