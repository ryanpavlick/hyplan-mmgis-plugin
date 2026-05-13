# Security Policy

## Supported versions

hyplan-mmgis-plugin is pre-1.0 software.  Security updates are provided
for the latest 0.x release only.

| Version  | Supported          |
|----------|--------------------|
| 0.1.x    | :white_check_mark: |
| < 0.1    | :x:                |

## Reporting a vulnerability

If you discover a security vulnerability in hyplan-mmgis-plugin, please
report it **privately** so we can investigate before public disclosure.

Preferred: use GitHub's Private Vulnerability Reporting at
<https://github.com/ryanpavlick/hyplan-mmgis-plugin/security/advisories/new>.

Alternative: email <ryan.p.pavlick@nasa.gov> with the subject line
"hyplan-mmgis-plugin security vulnerability".

Please include:

- A description of the vulnerability and its impact
- Which component is affected: service, frontend, or both
- Steps to reproduce
- The plugin version, HyPlan version, MMGIS version, and Python version
  where you observed it
- Any suggested fix or mitigation

You can expect:

- An initial acknowledgement within 5 business days
- A status update within 14 days
- Coordinated disclosure once a fix is released

## Scope

This policy covers the hyplan-mmgis-plugin repository itself: the
FastAPI service in `service/` and the MMGIS tool in `mmgis-tool/`.

It does **not** cover vulnerabilities in upstream projects:

- [HyPlan](https://github.com/ryanpavlick/hyplan) — report at the HyPlan
  repository.
- [MMGIS](https://github.com/NASA-AMMOS/MMGIS) — report at the MMGIS
  repository.
- Third-party Python dependencies (fastapi, uvicorn, xarray, etc.) —
  please report those upstream.
- External tile / data services proxied by the service (vfrmap.com,
  NASA GIBS, NOAA wind providers).
