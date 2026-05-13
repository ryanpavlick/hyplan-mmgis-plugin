"""FastAPI router modules.

Each submodule exposes a ``router: APIRouter`` containing one functional
group of endpoints.  ``service.app`` mounts them all under the root
prefix.

Routers are grouped by concern, not by HTTP method:

- :mod:`.metadata`   - ``/health``, ``/aircraft``, ``/sensors``
- :mod:`.tiles`      - ``/faa-tile/...``, ``/imagery-layers``
- :mod:`.wind`       - ``/wind-grid``
- :mod:`.generate`   - ``/generate-lines``
- :mod:`.compute`    - ``/compute-plan``, ``/optimize-sequence``
- :mod:`.export`     - ``/export``, ``/download/{...}``
- :mod:`.analysis`   - ``/generate-swaths``, ``/compute-glint``,
                       ``/optimize-azimuth``, ``/solar-position``
- :mod:`.lines`      - ``/add-line``, ``/edit-line``, ``/delete-line``,
                       ``/transform-lines``
- :mod:`.patterns`   - ``/generate-pattern``, ``/delete-pattern``,
                       ``/replace-pattern``, ``/patterns/{campaign_id}``
- :mod:`.campaigns`  - ``/campaigns``, ``/campaigns/{campaign_id}``
"""
