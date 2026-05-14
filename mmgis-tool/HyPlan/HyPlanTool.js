import $ from 'jquery'
import L_ from '../../Basics/Layers_/Layers_'
import Map_ from '../../Basics/Map_/Map_'
import {
    formatUtcOffset,
    getErrorMessage,
    glintColor,
    parseLocalDateTimeToUtcIso,
} from './helpers.js'
import './HyPlanTool.css'

// Browser-side controller for the HyPlan MMGIS plugin. This file owns the
// panel UI, map interaction modes, Leaflet overlay lifecycle, and translation
// between MMGIS state and the FastAPI service.

// Default service URL (can be overridden in config)
let SERVICE_URL = 'http://localhost:8100'

// Tool state shared across handlers. This includes the active campaign
// identity, current line/pattern selections, and references to plugin-owned
// Leaflet layers so we can replace them cleanly.
let campaignId = null
let flightLineLayer = null
let planLayer = null
let selectedLineIds = []
let windLayer = null
let patternsLayer = null            // map layer for all patterns (waypoint + line-pattern decoration)
let patternsCache = []              // [{pattern_id, kind, name, is_line_based, ...}]
let patternRefsForCompute = []      // pattern_ids of waypoint patterns to include in compute
let swathLayer = null
let glintLayer = null
let glintArcLayer = null   // swath polygon + colored dots for the most-recently-generated glint_arc
let drawLineMode = false
let drawLineStart = null
let patternCenter = null
let solarMarkerLayer = null

// --- Self-heal against MMGIS layer-state resets ---------------------------
// MMGIS toggling a Draw file / layer in the Layers panel can collaterally
// remove layers we added directly to Map_.map. Tag our layers with
// _hyplanOwned and re-add them if they're removed without our consent.
// Callers use hyplanOwn() when adding and hyplanDisownAndRemove() when
// intentionally removing so the layerremove listener knows the difference.

function hyplanOwn(layer) {
    if (!layer) return layer
    layer._hyplanOwned = true
    return layer
}

function hyplanDisownAndRemove(layer) {
    if (!layer) return
    layer._hyplanOwned = false
    if (Map_.map && Map_.map.hasLayer(layer)) {
        Map_.map.removeLayer(layer)
    }
}

let _hyplanSelfHealInstalled = false
function installHyplanSelfHeal() {
    if (_hyplanSelfHealInstalled || !Map_ || !Map_.map) return
    Map_.map.on('layerremove', function (e) {
        const layer = e.layer
        if (!layer || !layer._hyplanOwned) return
        // Re-add on next tick so we don't race any further synchronous ops
        // from the caller that removed us (e.g. an MMGIS toggle cascade).
        setTimeout(function () {
            if (layer._hyplanOwned && !Map_.map.hasLayer(layer)) {
                layer.addTo(Map_.map)
            }
        }, 50)
    })
    _hyplanSelfHealInstalled = true
}

// The numbered sections in this panel mirror the intended planning workflow,
// from campaign setup through generation, analysis, and export.
const markup = `
<div id="hyplanTool">
    <h3>HyPlan Flight Planner</h3>

    <details class="hyplan-section" open>
        <summary>1. Campaign</summary>
        <label>Campaign Name</label>
        <input type="text" id="hyplan-campaign-name" value="Mission" />
        <label>Aircraft</label>
        <select id="hyplan-aircraft">
            <option value="KingAirB200" selected>Loading...</option>
        </select>
        <label>Sensor</label>
        <select id="hyplan-sensor">
            <option value="AVIRIS3" selected>Loading...</option>
        </select>
        <label>Takeoff Airport (ICAO)</label>
        <input type="text" id="hyplan-takeoff-airport" value="" placeholder="e.g. KPMD" />
        <label>Return Airport (ICAO, blank = same as takeoff)</label>
        <input type="text" id="hyplan-return-airport" value="" placeholder="" />
        <label>Takeoff Time (local browser time)</label>
        <input type="datetime-local" id="hyplan-takeoff-time" value="" />
        <div class="hyplan-meta">Enter local time here. HyPlan converts it to UTC before calling the service.</div>
        <div id="hyplan-takeoff-time-meta" class="hyplan-meta"></div>
        <label>Wind</label>
        <select id="hyplan-wind-kind">
            <option value="still_air" selected>Still Air</option>
            <option value="constant">Constant Wind</option>
            <option value="gfs">GFS Forecast</option>
            <option value="gmao">GMAO GEOS-FP</option>
        </select>
        <div id="hyplan-wind-params" style="display:none">
            <label>Wind Speed (kt)</label>
            <input type="number" id="hyplan-wind-speed" value="0" />
            <label>Wind Direction (deg from)</label>
            <input type="number" id="hyplan-wind-direction" value="0" />
        </div>
        <button id="hyplan-show-wind-btn">Show Wind on Map</button>
        <button id="hyplan-hide-wind-btn" style="display:none">Hide Wind</button>
        <div id="hyplan-wind-status" class="hyplan-status"></div>

        <details style="margin-top:0.5em">
            <summary style="cursor:pointer; font-size:11px; color:var(--color-c)">Import / Export (JSON bundle)</summary>
            <p style="font-size:11px; color:var(--color-c)">Round-trip the active campaign as a single JSON file — useful for sharing a mission between machines or keeping a quick backup.  Export artifacts (KML / GPX) are excluded; they regenerate from Compute Plan + Export.</p>
            <button id="hyplan-export-campaign-btn" disabled>Export campaign</button>
            <label style="display:inline-block; margin:4px 0">
                <input type="file" id="hyplan-import-campaign-file" accept=".json,application/json" style="display:none" />
                <button id="hyplan-import-campaign-btn" type="button">Import campaign...</button>
            </label>
            <div id="hyplan-campaign-io-status" class="hyplan-status"></div>
        </details>
    </details>

    <details class="hyplan-section">
        <summary>2. Generate Flight Lines</summary>
        <p style="font-size:11px; color:var(--color-c)">Draw a polygon on the map using the Draw tool, then click Generate.</p>
        <label>Altitude (m MSL)</label>
        <input type="number" id="hyplan-altitude" value="3000" />
        <label>Overlap (%)</label>
        <input type="number" id="hyplan-overlap" value="20" />
        <label>Azimuth (blank = auto)</label>
        <input type="text" id="hyplan-azimuth" value="" />
        <button id="hyplan-optimize-azimuth-btn">Optimize Azimuth (min glint)</button>
        <div id="hyplan-optimize-azimuth-status" class="hyplan-status"></div>
        <div id="hyplan-optimize-azimuth-plot"></div>
        <button id="hyplan-generate-btn">Generate Flight Box</button>
        <div id="hyplan-generate-status" class="hyplan-status"></div>
    </details>

    <details class="hyplan-section">
        <summary>2b. Individual Lines</summary>
        <p style="font-size:11px; color:var(--color-c)">Click two points on the map to add a line, or enter coordinates.</p>
        <button id="hyplan-add-line-btn">Draw Line on Map</button>
        <button id="hyplan-cancel-draw-btn" style="display:none">Cancel</button>
        <button id="hyplan-delete-line-btn" disabled>Delete Selected Line</button>
        <div id="hyplan-add-line-status" class="hyplan-status"></div>

        <details style="margin-top:0.5em">
            <summary style="cursor:pointer; font-size:11px; color:var(--color-c)">Relative-to calculator (bearing + distance from anchor)</summary>
            <p style="font-size:11px; color:var(--color-c)">Compute a geodesic offset from an anchor point. Use it to place the second endpoint of a line you've started drawing, or copy the result into the line editor.</p>
            <label>Anchor lat / lon</label>
            <div style="display:flex; gap:4px">
                <input type="number" id="hyplan-rel-anchor-lat" step="any" placeholder="lat" />
                <input type="number" id="hyplan-rel-anchor-lon" step="any" placeholder="lon" />
            </div>
            <label>Bearing (deg, true)</label>
            <input type="number" id="hyplan-rel-bearing" step="any" value="0" />
            <label>Distance (m)</label>
            <input type="number" id="hyplan-rel-distance" step="any" value="1000" />
            <button id="hyplan-rel-compute-btn">Compute</button>
            <button id="hyplan-rel-use-as-end-btn" style="display:none">Use as line endpoint</button>
            <div id="hyplan-rel-status" class="hyplan-status"></div>
        </details>
    </details>

    <details class="hyplan-section">
        <summary>2c. Flight Patterns</summary>
        <p style="font-size:11px; color:var(--color-c)">Click a point on the map to set the center, then generate.</p>
        <label>Pattern</label>
        <select id="hyplan-pattern-type">
            <option value="racetrack">Racetrack</option>
            <option value="rosette">Rosette</option>
            <option value="polygon">Polygon</option>
            <option value="sawtooth">Sawtooth</option>
            <option value="spiral">Spiral</option>
            <option value="glint_arc">Glint Arc</option>
        </select>
        <label>Heading (deg)</label>
        <input type="number" id="hyplan-pattern-heading" value="0" />
        <div id="hyplan-pattern-params"></div>
        <button id="hyplan-set-pattern-center-btn">Set Center on Map</button>
        <button id="hyplan-cancel-pattern-btn" style="display:none">Cancel</button>
        <button id="hyplan-generate-pattern-btn" disabled>Generate Pattern</button>
        <div id="hyplan-pattern-status" class="hyplan-status"></div>
    </details>

    <details class="hyplan-section">
        <summary>2d. Patterns in Campaign</summary>
        <p style="font-size:11px; color:var(--color-c)">Delete a pattern as a whole. Line patterns also show each leg in the line list above for individual selection/transform.</p>
        <div id="hyplan-patterns-list" class="hyplan-patterns-list"></div>
    </details>

    <details class="hyplan-section">
        <summary>2e. Move Pattern</summary>
        <p style="font-size:11px; color:var(--color-c)">Move a whole pattern in place: shift it N/E, re-anchor at a lat/lon, or rotate it about its center. The pattern keeps its identity and any compute-sequence references stay valid.</p>
        <label>Pattern</label>
        <select id="hyplan-move-pattern-select"></select>
        <label>Operation</label>
        <select id="hyplan-move-pattern-op">
            <option value="translate">Translate (m N/E)</option>
            <option value="move_to">Move to (lat/lon)</option>
            <option value="rotate">Rotate (deg, about center)</option>
        </select>
        <div id="hyplan-move-pattern-params">
            <label id="hyplan-move-pattern-label-1">North (m)</label>
            <input type="number" id="hyplan-move-pattern-val-1" value="0" />
            <div id="hyplan-move-pattern-val-2-wrap">
                <label id="hyplan-move-pattern-label-2">East (m)</label>
                <input type="number" id="hyplan-move-pattern-val-2" value="0" />
            </div>
        </div>
        <button id="hyplan-move-pattern-btn" disabled>Apply</button>
        <div id="hyplan-move-pattern-status" class="hyplan-status"></div>
    </details>

    <details class="hyplan-section">
        <summary>3. Select & Order Lines</summary>
        <button id="hyplan-select-all-btn">Select All</button>
        <button id="hyplan-clear-selection-btn">Clear</button>
        <button id="hyplan-optimize-btn" disabled>Optimize Order</button>
        <div id="hyplan-line-list" class="hyplan-line-list"></div>
        <div id="hyplan-selection-status" class="hyplan-status"></div>
        <div id="hyplan-optimize-status" class="hyplan-status"></div>
    </details>

    <details class="hyplan-section">
        <summary>3b. Transform Selected Lines</summary>
        <label>Operation</label>
        <select id="hyplan-transform-op">
            <option value="rotate">Rotate (deg)</option>
            <option value="offset_across">Offset Across Track (m)</option>
            <option value="offset_along">Offset Along Track (m)</option>
            <option value="offset_north_east">Shift N/E (m)</option>
            <option value="reverse">Reverse Direction</option>
        </select>
        <div id="hyplan-transform-params">
            <label id="hyplan-transform-label-1">Angle (deg)</label>
            <input type="number" id="hyplan-transform-val-1" value="0" />
            <div id="hyplan-transform-val-2-wrap" style="display:none">
                <label id="hyplan-transform-label-2">Value 2</label>
                <input type="number" id="hyplan-transform-val-2" value="0" />
            </div>
        </div>
        <button id="hyplan-transform-btn" disabled>Apply Transform</button>
        <div id="hyplan-transform-status" class="hyplan-status"></div>
    </details>

    <details class="hyplan-section">
        <summary>4. Compute Flight Plan</summary>
        <button id="hyplan-compute-btn" disabled>Compute Plan</button>
        <button id="hyplan-clear-plan-btn" style="display:none">Clear Plan</button>
        <div id="hyplan-compute-status" class="hyplan-status"></div>
        <div id="hyplan-summary"></div>
    </details>

    <details class="hyplan-section">
        <summary>4b. Swath Display</summary>
        <button id="hyplan-show-swaths-btn" disabled>Generate Swaths</button>
        <button id="hyplan-hide-swaths-btn" style="display:none">Hide Swaths</button>
        <div id="hyplan-swath-status" class="hyplan-status"></div>
    </details>

    <details class="hyplan-section">
        <summary>4c. Glint Analysis</summary>
        <p style="font-size:11px; color:var(--color-c)">For each selected line at the takeoff time and current sensor, plots per-swath-sample glint angle. Rotate or shift selected lines (Section 3b) or change the takeoff time (Section 1) and re-compute to minimize glint.</p>
        <label>Glint threshold (deg)</label>
        <input type="number" id="hyplan-glint-threshold" value="25" step="1" min="1" max="90" />
        <button id="hyplan-show-glint-btn" disabled>Compute Glint</button>
        <button id="hyplan-hide-glint-btn" style="display:none">Hide Glint</button>
        <div id="hyplan-glint-status" class="hyplan-status"></div>
        <div id="hyplan-glint-summary"></div>
    </details>

    <details class="hyplan-section">
        <summary>5. Solar Position</summary>
        <p style="font-size:11px; color:var(--color-c)">Plot solar zenith angle through the day at a chosen point. Pick a location on the map, or use the midpoint of a selected flight line.</p>
        <button id="hyplan-pick-solar-point-btn">Plot at Map Point</button>
        <button id="hyplan-cancel-pick-solar-btn" style="display:none">Cancel</button>
        <button id="hyplan-solar-from-line-btn" disabled>Plot at Selected Line</button>
        <button id="hyplan-hide-solar-btn" style="display:none">Hide Plot</button>
        <div id="hyplan-solar-status" class="hyplan-status"></div>
        <div id="hyplan-solar-plot"></div>
        <div id="hyplan-solar-meta" style="font-size:11px; color:var(--color-c); margin-top:4px"></div>
    </details>

    <details class="hyplan-section">
        <summary>6. Export</summary>
        <button id="hyplan-export-btn" disabled>Export KML + GPX</button>
        <div id="hyplan-export-status" class="hyplan-status"></div>
        <div id="hyplan-download-links"></div>
    </details>
</div>
`

const HyPlanTool = {
    height: 0,
    width: 320,
    MMGISInterface: null,
    make: function () {
        this.MMGISInterface = new interfaceWithMMGIS()
    },
    destroy: function () {
        this.MMGISInterface.separateFromMMGIS()
    },
    getUrlString: function () {
        return ''
    },
}

function interfaceWithMMGIS() {
    // Bootstrap the tool inside the MMGIS panel: mount markup, read config,
    // query service metadata, and bind the UI + map event handlers.
    this.separateFromMMGIS = function () {
        separateFromMMGIS()
    }

    installHyplanSelfHeal()

    const toolsContainer = $('#toolPanel')
    toolsContainer.css('background', 'var(--color-k)')
    toolsContainer.empty()
    toolsContainer.html('<div style="height: 100%">' + markup + '</div>')

    // Wind kind toggle
    $('#hyplan-wind-kind').on('change', function () {
        if ($(this).val() === 'constant') {
            $('#hyplan-wind-params').show()
        } else {
            $('#hyplan-wind-params').hide()
        }
    })
    $('#hyplan-takeoff-time').on('input change', updateTakeoffTimeMeta)
    updateTakeoffTimeMeta()

    // --- Campaign Import / Export ---------------------------------------
    // Round-trip the active campaign as a single JSON bundle via the
    // /campaigns/{id}/export and /campaigns/import endpoints.  Useful
    // for sharing a mission between machines or keeping a quick backup.

    function updateCampaignIOEnabled() {
        $('#hyplan-export-campaign-btn').prop('disabled', !campaignId)
    }
    updateCampaignIOEnabled()

    $('#hyplan-export-campaign-btn').on('click', function () {
        if (!campaignId) return
        $('#hyplan-campaign-io-status').text('Exporting...')
        fetch(`${SERVICE_URL}/campaigns/${campaignId}/export`)
            .then(r => r.json())
            .then(bundle => {
                if (bundle.detail) {
                    $('#hyplan-campaign-io-status').text('Failed: ' + getErrorMessage(bundle))
                    return
                }
                const text = JSON.stringify(bundle, null, 2)
                const blob = new Blob([text], { type: 'application/json' })
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                const safeName = (bundle.name || 'mission').replace(/[^\w.-]+/g, '_')
                a.href = url
                a.download = `${safeName}_campaign.json`
                document.body.appendChild(a)
                a.click()
                document.body.removeChild(a)
                URL.revokeObjectURL(url)
                $('#hyplan-campaign-io-status').text(
                    `Exported ${bundle.name} (${Object.keys(bundle.files).length} files).`
                )
            })
            .catch(err => {
                $('#hyplan-campaign-io-status').text('Error: ' + err.message)
            })
    })

    // The visible "Import..." button proxies clicks to the hidden
    // <input type="file"> so we can style the button consistently
    // with the rest of the panel.
    $('#hyplan-import-campaign-btn').on('click', function (e) {
        e.preventDefault()
        $('#hyplan-import-campaign-file').trigger('click')
    })

    $('#hyplan-import-campaign-file').on('change', function (e) {
        const file = e.target.files && e.target.files[0]
        if (!file) return
        $('#hyplan-campaign-io-status').text(`Importing ${file.name}...`)
        const reader = new FileReader()
        reader.onload = function (ev) {
            let bundle
            try {
                bundle = JSON.parse(ev.target.result)
            } catch (err) {
                $('#hyplan-campaign-io-status').text('Not a JSON file: ' + err.message)
                return
            }
            fetch(`${SERVICE_URL}/campaigns/import`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bundle: bundle, replace: false }),
            })
                .then(r => r.json())
                .then(data => {
                    if (data.detail) {
                        $('#hyplan-campaign-io-status').text('Failed: ' + getErrorMessage(data))
                        return
                    }
                    // Adopt the imported campaign as the active one.
                    campaignId = data.campaign_id
                    $('#hyplan-campaign-name').val(data.name || '')
                    displayFlightLines(data.flight_lines)
                    updateLineList(data.flight_lines)
                    renderPatternsLayer(data.patterns)
                    $('#hyplan-campaign-io-status').text(
                        `Imported ${data.name} as new campaign ${data.campaign_id}.`
                    )
                    updateCampaignIOEnabled()
                })
                .catch(err => {
                    $('#hyplan-campaign-io-status').text('Error: ' + err.message)
                })
        }
        reader.readAsText(file)
        // Reset so the same file can be re-picked.
        e.target.value = ''
    })

    // Re-evaluate Export button enablement whenever campaignId might
    // have changed.  We don't have a global "campaign changed" event
    // yet, so just check before showing the panel and on a periodic
    // tick.  Cheaper than wiring every code path that sets campaignId.
    setInterval(updateCampaignIOEnabled, 1500)

    // Show Wind on Map
    $('#hyplan-show-wind-btn').on('click', function () {
        const windKind = $('#hyplan-wind-kind').val()
        if (windKind !== 'gfs' && windKind !== 'gmao') {
            $('#hyplan-wind-status').text('Select GFS or GMAO wind source to visualize.')
            return
        }

        const bounds = Map_.map.getBounds()
        const altitude = parseFloat($('#hyplan-altitude').val()) || 3000
        const time = getTakeoffTimeUtcIso() || new Date().toISOString()

        $('#hyplan-wind-status').text('Fetching wind data...')
        $('#hyplan-show-wind-btn').prop('disabled', true)

        fetch(`${SERVICE_URL}/wind-grid`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                source: windKind,
                bounds: [
                    bounds.getWest(),
                    bounds.getSouth(),
                    bounds.getEast(),
                    bounds.getNorth(),
                ],
                time: time,
                altitude_m: altitude,
            }),
        })
        .then(r => {
            if (!r.ok) return r.json().then(d => { throw new Error(getErrorMessage(d)) })
            return r.json()
        })
        .then(data => {
            hyplanDisownAndRemove(windLayer)
            windLayer = window.L.velocityLayer({
                displayValues: true,
                displayOptions: {
                    velocityType: 'Wind',
                    displayPosition: 'bottomleft',
                    displayEmptyString: 'No wind data',
                },
                data: data,
                maxVelocity: 30,
                velocityScale: 0.005,
                particleAge: 90,
                lineWidth: 1.5,
                particleMultiplier: 1 / 300,
                frameRate: 15,
            })
            hyplanOwn(windLayer).addTo(Map_.map)
            $('#hyplan-wind-status').text(`Wind streamlines displayed (${windKind.toUpperCase()}).`)
            $('#hyplan-show-wind-btn').hide()
            $('#hyplan-hide-wind-btn').show()
        })
        .catch(err => {
            $('#hyplan-wind-status').text('Error: ' + err.message)
        })
        .finally(() => {
            $('#hyplan-show-wind-btn').prop('disabled', false)
        })
    })

    // Hide Wind
    $('#hyplan-hide-wind-btn').on('click', function () {
        hyplanDisownAndRemove(windLayer)
        windLayer = null
        $('#hyplan-wind-status').text('')
        $('#hyplan-hide-wind-btn').hide()
        $('#hyplan-show-wind-btn').show()
    })

    // Read service URL from config if available
    try {
        const vars = L_.configData.tools.find(t => t.name === 'HyPlan')
        if (vars && vars.variables && vars.variables.serviceUrl) {
            SERVICE_URL = vars.variables.serviceUrl
        }
    } catch (e) { /* use default */ }

    // Check service connectivity
    fetch(`${SERVICE_URL}/health`)
        .then(r => r.json())
        .then(data => {
            if (data.status === 'ok') {
                $('#hyplan-generate-status').text(`Connected to HyPlan service v${data.service_version}`)
            }
        })
        .catch(() => {
            $('#hyplan-generate-status').html(
                '<span style="color:#ef4444">Cannot connect to HyPlan service. ' +
                'Check that the service is running and the URL is correct.</span>'
            )
        })

    // Load aircraft and sensor lists from service
    fetch(`${SERVICE_URL}/aircraft`)
        .then(r => r.json())
        .then(data => {
            const sel = $('#hyplan-aircraft')
            sel.empty()
            data.aircraft.forEach(name => {
                const selected = name === 'KingAirB200' ? ' selected' : ''
                sel.append(`<option value="${name}"${selected}>${name}</option>`)
            })
        })
        .catch(() => {})

    fetch(`${SERVICE_URL}/sensors`)
        .then(r => r.json())
        .then(data => {
            const sel = $('#hyplan-sensor')
            sel.empty()
            data.sensors.forEach(name => {
                const selected = name === 'AVIRIS3' ? ' selected' : ''
                sel.append(`<option value="${name}"${selected}>${name}</option>`)
            })
        })
        .catch(() => {})

    // Reset state
    campaignId = null
    selectedLineIds = []

    // Get map bounds for campaign
    const bounds = Map_.map.getBounds()
    const campaignBounds = [
        bounds.getWest(),
        bounds.getSouth(),
        bounds.getEast(),
        bounds.getNorth(),
    ]

    // --- Optimize Azimuth (min glint) ---
    $('#hyplan-optimize-azimuth-btn').on('click', function () {
        const polygon = getDrawnPolygon()
        if (!polygon) {
            $('#hyplan-optimize-azimuth-status').text('Error: Draw a polygon first; the azimuth sweep uses its centroid as the test point.')
            return
        }
        const altVal = parseFloat($('#hyplan-altitude').val())
        if (isNaN(altVal) || altVal <= 0) {
            $('#hyplan-optimize-azimuth-status').text('Error: Altitude must be a positive number.')
            return
        }
        const sensor = $('#hyplan-sensor').val()
        if (!sensor) {
            $('#hyplan-optimize-azimuth-status').text('Error: Select a sensor first.')
            return
        }
        const takeoffTimeVal = $('#hyplan-takeoff-time').val()
        if (!takeoffTimeVal) {
            $('#hyplan-optimize-azimuth-status').text('Error: Set a takeoff time first — Section 1.')
            return
        }
        const takeoffTime = getTakeoffTimeUtcIso()
        if (!takeoffTime) {
            $('#hyplan-optimize-azimuth-status').text('Error: Takeoff time is invalid.')
            return
        }
        const centroid = polygonCentroid(polygon)
        if (!centroid) {
            $('#hyplan-optimize-azimuth-status').text('Error: Could not compute polygon centroid.')
            return
        }

        $('#hyplan-optimize-azimuth-status').text('Sweeping headings…')
        $('#hyplan-optimize-azimuth-btn').prop('disabled', true)

        fetch(`${SERVICE_URL}/optimize-azimuth`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                lat: centroid.lat,
                lon: centroid.lon,
                altitude_msl_m: altVal,
                sensor: sensor,
                takeoff_time: takeoffTime,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.detail || data.message) {
                $('#hyplan-optimize-azimuth-status').text('Error: ' + getErrorMessage(data))
                return
            }
            $('#hyplan-azimuth').val(data.optimal_azimuth.toFixed(0))
            renderAzimuthSweepPlot(data)
            const warnPrefix = data.sun_below_horizon ? '⚠ Sun below horizon — ' : ''
            $('#hyplan-optimize-azimuth-status').html(
                `${warnPrefix}Optimal azimuth: <b>${data.optimal_azimuth.toFixed(0)}°</b> ` +
                `(${data.criterion === 'max_mean' ? 'mean' : 'min'} glint ${data.optimal_value.toFixed(1)}°). ` +
                `Filled into the field above.`
            )
        })
        .catch(err => {
            $('#hyplan-optimize-azimuth-status').text('Error: ' + err.message)
        })
        .finally(() => {
            $('#hyplan-optimize-azimuth-btn').prop('disabled', false)
        })
    })

    // --- Generate button ---
    $('#hyplan-generate-btn').on('click', function () {
        const polygon = getDrawnPolygon()
        if (!polygon) {
            $('#hyplan-generate-status').text('Error: Draw a polygon on the map first using the Draw tool.')
            return
        }
        const altVal = parseFloat($('#hyplan-altitude').val())
        if (isNaN(altVal) || altVal <= 0) {
            $('#hyplan-generate-status').text('Error: Altitude must be a positive number.')
            return
        }
        const overlapVal = parseFloat($('#hyplan-overlap').val())
        if (isNaN(overlapVal) || overlapVal < 0 || overlapVal >= 100) {
            $('#hyplan-generate-status').text('Error: Overlap must be between 0 and 100.')
            return
        }

        const sensor = $('#hyplan-sensor').val()
        const altitude = parseFloat($('#hyplan-altitude').val()) || 3000
        const overlap = parseFloat($('#hyplan-overlap').val()) || 20
        const azimuthText = $('#hyplan-azimuth').val().trim()
        const azimuth = azimuthText ? parseFloat(azimuthText) : null
        const name = $('#hyplan-campaign-name').val() || 'Mission'

        $('#hyplan-generate-status').text('Generating...')
        $('#hyplan-generate-btn').prop('disabled', true)

        fetch(`${SERVICE_URL}/generate-lines`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId || 'campaign-' + Date.now(),
                campaign_name: name,
                campaign_bounds: campaignBounds,
                generator: {
                    kind: 'box_around_polygon',
                    params: {
                        sensor: sensor,
                        altitude_msl_m: altitude,
                        overlap_pct: overlap,
                        azimuth: azimuth,
                        box_name: name,
                    },
                },
                geometry: polygon,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.detail || data.message) {
                $('#hyplan-generate-status').text('Error: ' + getErrorMessage(data))
                return
            }
            campaignId = data.campaign_id
            displayFlightLines(data.flight_lines)
            updateLineList(data.flight_lines)
            $('#hyplan-generate-status').text(
                `Generated ${data.summary.line_count} flight lines.`
            )
        })
        .catch(err => {
            $('#hyplan-generate-status').text('Error: ' + err.message)
        })
        .finally(() => {
            $('#hyplan-generate-btn').prop('disabled', false)
        })
    })

    // --- Select All / Clear ---
    $('#hyplan-select-all-btn').on('click', function () {
        $('.hyplan-line-item').addClass('selected')
        selectedLineIds = []
        $('.hyplan-line-item').each(function () {
            selectedLineIds.push($(this).data('lineid'))
        })
        updateSelectionStatus()
    })

    $('#hyplan-clear-selection-btn').on('click', function () {
        $('.hyplan-line-item').removeClass('selected')
        selectedLineIds = []
        updateSelectionStatus()
    })

    // --- Optimize button ---
    $('#hyplan-optimize-btn').on('click', function () {
        if (!campaignId || selectedLineIds.length < 2) {
            $('#hyplan-optimize-status').text('Select at least 2 lines to optimize.')
            return
        }

        const aircraft = $('#hyplan-aircraft').val()
        const takeoffAirport = $('#hyplan-takeoff-airport').val().trim().toUpperCase()
        if (!takeoffAirport) {
            $('#hyplan-optimize-status').text('Set a takeoff airport first.')
            return
        }
        const returnAirport = $('#hyplan-return-airport').val().trim().toUpperCase() || takeoffAirport

        $('#hyplan-optimize-status').text('Optimizing...')
        $('#hyplan-optimize-btn').prop('disabled', true)

        fetch(`${SERVICE_URL}/optimize-sequence`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId,
                line_ids: selectedLineIds,
                aircraft: aircraft,
                takeoff_airport: takeoffAirport,
                return_airport: returnAirport,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.detail || data.message) {
                $('#hyplan-optimize-status').text('Error: ' + getErrorMessage(data))
                return
            }
            // Apply the optimized order
            selectedLineIds = data.proposed_sequence.map(e => e.line_id)
            // Update the list UI to reflect new order
            const list = $('#hyplan-line-list')
            selectedLineIds.forEach(lid => {
                const item = $(`.hyplan-line-item[data-lineid="${lid}"]`)
                item.addClass('selected')
                list.append(item)
            })
            updateSelectionStatus()
            const time = data.total_time ? ` (${(data.total_time * 60).toFixed(0)} min)` : ''
            $('#hyplan-optimize-status').text(
                `Optimized: ${data.lines_covered} lines${time}.` +
                (data.lines_skipped.length > 0 ? ` Skipped: ${data.lines_skipped.join(', ')}` : '')
            )
        })
        .catch(err => {
            $('#hyplan-optimize-status').text('Error: ' + err.message)
        })
        .finally(() => {
            $('#hyplan-optimize-btn').prop('disabled', false)
        })
    })

    // --- Compute button ---
    $('#hyplan-compute-btn').on('click', function () {
        if (selectedLineIds.length === 0 && patternRefsForCompute.length === 0) {
            $('#hyplan-compute-status').text('Select flight lines or include a pattern first.')
            return
        }

        const aircraft = $('#hyplan-aircraft').val()
        // Build sequence: selected flight lines, then any waypoint patterns marked for compute.
        const sequence = selectedLineIds.map(lid => ({
            kind: 'line',
            line_id: lid,
        }))
        patternRefsForCompute.forEach(pid => {
            sequence.push({ kind: 'pattern', pattern_id: pid })
        })

        // Build wind config
        const windKind = $('#hyplan-wind-kind').val()
        let wind = { kind: windKind }
        if (windKind === 'constant') {
            wind.speed_kt = parseFloat($('#hyplan-wind-speed').val()) || 0
            wind.direction_deg = parseFloat($('#hyplan-wind-direction').val()) || 0
        }

        // Airports
        const takeoffAirport = $('#hyplan-takeoff-airport').val().trim().toUpperCase() || null
        const returnAirport = $('#hyplan-return-airport').val().trim().toUpperCase() || takeoffAirport

        // Takeoff time
        const takeoffTime = getTakeoffTimeUtcIso()

        $('#hyplan-compute-status').text('Computing...')
        $('#hyplan-compute-btn').prop('disabled', true)

        fetch(`${SERVICE_URL}/compute-plan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId,
                sequence: sequence,
                aircraft: aircraft,
                wind: wind,
                takeoff_airport: takeoffAirport,
                return_airport: returnAirport,
                takeoff_time: takeoffTime,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.detail || data.message) {
                $('#hyplan-compute-status').text('Error: ' + getErrorMessage(data))
                return
            }
            displayPlan(data.segments)
            const s = data.summary
            $('#hyplan-summary').html(
                `<b>Segments:</b> ${s.segments}<br>` +
                `<b>Distance:</b> ${s.total_distance_nm.toFixed(1)} nm<br>` +
                `<b>Time:</b> ${s.total_time_min.toFixed(1)} min`
            )
            $('#hyplan-compute-status').text('Plan computed.')
            $('#hyplan-export-btn').prop('disabled', false)
            $('#hyplan-clear-plan-btn').show()
        })
        .catch(err => {
            $('#hyplan-compute-status').text('Error: ' + err.message)
        })
        .finally(() => {
            $('#hyplan-compute-btn').prop('disabled', false)
        })
    })

    // --- Clear Plan ---
    $('#hyplan-clear-plan-btn').on('click', function () {
        hyplanDisownAndRemove(planLayer)
        planLayer = null
        $('#hyplan-summary').empty()
        $('#hyplan-compute-status').text('')
        $('#hyplan-export-btn').prop('disabled', true)
        $('#hyplan-export-status').text('')
        $('#hyplan-download-links').empty()
        $('#hyplan-clear-plan-btn').hide()
    })

    // --- Generate Swaths ---
    $('#hyplan-show-swaths-btn').on('click', function () {
        if (!campaignId || selectedLineIds.length === 0) {
            $('#hyplan-swath-status').text('Select lines first.')
            return
        }
        const sensor = $('#hyplan-sensor').val()
        if (!sensor) {
            $('#hyplan-swath-status').text('Select a sensor first.')
            return
        }
        const altitude = parseFloat($('#hyplan-altitude').val()) || 3000

        $('#hyplan-swath-status').text('Generating swaths...')
        $('#hyplan-show-swaths-btn').prop('disabled', true)

        // If a polygon is currently drawn on the map, send it as the
        // coverage target so the service can score what fraction of
        // it the selected swaths actually cover.
        const targetPolygon = getDrawnPolygon()

        fetch(`${SERVICE_URL}/generate-swaths`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId,
                line_ids: selectedLineIds,
                sensor: sensor,
                altitude_msl_m: altitude,
                target_polygon: targetPolygon || null,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.detail || data.message) {
                $('#hyplan-swath-status').text('Error: ' + getErrorMessage(data))
                return
            }
            hyplanDisownAndRemove(swathLayer)
            swathLayer = hyplanOwn(window.L.geoJSON(data.swaths, {
                style: {
                    color: '#8b5cf6',
                    weight: 1,
                    fillColor: '#8b5cf6',
                    fillOpacity: 0.15,
                },
                onEachFeature: function (feature, layer) {
                    layer.bindTooltip(feature.properties.site_name || '', { sticky: true })
                },
            })).addTo(Map_.map)

            let status = `${data.count} swath(s) displayed.`
            if (data.gap_overlap && data.gap_overlap.total_pairs > 0) {
                status += ` Overlaps: ${data.gap_overlap.overlapping_pairs}, Gaps: ${data.gap_overlap.gap_pairs}`
            }
            if (typeof data.coverage_fraction === 'number') {
                const pct = (data.coverage_fraction * 100).toFixed(1)
                status += ` Coverage: ${pct}%`
            }
            $('#hyplan-swath-status').text(status)
            $('#hyplan-show-swaths-btn').hide()
            $('#hyplan-hide-swaths-btn').show()
        })
        .catch(err => {
            $('#hyplan-swath-status').text('Error: ' + err.message)
        })
        .finally(() => {
            $('#hyplan-show-swaths-btn').prop('disabled', false)
        })
    })

    // --- Hide Swaths ---
    $('#hyplan-hide-swaths-btn').on('click', function () {
        hyplanDisownAndRemove(swathLayer)
        swathLayer = null
        $('#hyplan-swath-status').text('')
        $('#hyplan-hide-swaths-btn').hide()
        $('#hyplan-show-swaths-btn').show()
    })

    // --- Compute Glint ---
    $('#hyplan-show-glint-btn').on('click', function () {
        if (!campaignId || selectedLineIds.length === 0) {
            $('#hyplan-glint-status').text('Select lines first.')
            return
        }
        const sensor = $('#hyplan-sensor').val()
        if (!sensor) {
            $('#hyplan-glint-status').text('Select a sensor first.')
            return
        }
        const takeoffTimeVal = $('#hyplan-takeoff-time').val()
        if (!takeoffTimeVal) {
            $('#hyplan-glint-status').text('Set a takeoff time in Section 1 first.')
            return
        }
        const takeoffTime = getTakeoffTimeUtcIso()
        if (!takeoffTime) {
            $('#hyplan-glint-status').text('Takeoff time is invalid.')
            return
        }
        const threshold = parseFloat($('#hyplan-glint-threshold').val()) || 25.0

        $('#hyplan-glint-status').text('Computing glint…')
        $('#hyplan-show-glint-btn').prop('disabled', true)

        fetch(`${SERVICE_URL}/compute-glint`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId,
                line_ids: selectedLineIds,
                sensor: sensor,
                takeoff_time: takeoffTime,
                threshold_deg: threshold,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.detail || data.message) {
                $('#hyplan-glint-status').text('Error: ' + getErrorMessage(data))
                return
            }
            hyplanDisownAndRemove(glintLayer)
            glintLayer = hyplanOwn(window.L.geoJSON(data.glint, {
                pointToLayer: function (feature, latlng) {
                    const ga = feature.properties.glint_angle
                    return window.L.circleMarker(latlng, {
                        radius: 3,
                        stroke: false,
                        fillColor: glintColor(ga),
                        fillOpacity: 0.85,
                        interactive: false,
                    })
                },
            })).addTo(Map_.map)

            renderGlintSummary(data.summary, data.threshold_deg)
            const nFeat = (data.glint && data.glint.features) ? data.glint.features.length : 0
            const status = $('#hyplan-glint-status')
            status.empty()
            if (data.sun_below_horizon) {
                status.append(
                    '<div style="color:#ef4444; font-weight:bold; padding:4px 0">' +
                    '⚠ Sun is below the horizon at this time &amp; location.' +
                    '</div>'
                )
                if (data.warnings && data.warnings.length) {
                    status.append(`<div>${data.warnings[0]}</div>`)
                }
            }
            status.append(`<div>${nFeat} samples plotted at threshold ${data.threshold_deg}°.</div>`)
            $('#hyplan-show-glint-btn').hide()
            $('#hyplan-hide-glint-btn').show()
        })
        .catch(err => {
            $('#hyplan-glint-status').text('Error: ' + err.message)
        })
        .finally(() => {
            $('#hyplan-show-glint-btn').prop('disabled', false)
        })
    })

    // --- Hide Glint ---
    $('#hyplan-hide-glint-btn').on('click', function () {
        hyplanDisownAndRemove(glintLayer)
        glintLayer = null
        $('#hyplan-glint-status').text('')
        $('#hyplan-glint-summary').empty()
        $('#hyplan-hide-glint-btn').hide()
        $('#hyplan-show-glint-btn').show()
    })

    // --- Export button ---
    $('#hyplan-export-btn').on('click', function () {
        if (!campaignId) return

        $('#hyplan-export-status').text('Exporting...')

        fetch(`${SERVICE_URL}/export`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId,
                formats: ['kml', 'gpx'],
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.detail || data.message) {
                $('#hyplan-export-status').text('Error: ' + getErrorMessage(data))
                return
            }
            const count = data.artifacts.length
            $('#hyplan-export-status').text(`Exported ${count} file(s).`)
            const links = $('#hyplan-download-links')
            links.empty()
            data.artifacts.forEach(a => {
                if (a.download_url) {
                    links.append(
                        `<a href="${SERVICE_URL}${a.download_url}" target="_blank" ` +
                        `style="display:block; color:#60a5fa; margin:2px 0; font-size:12px;">` +
                        `Download ${a.filename}</a>`
                    )
                }
            })
        })
        .catch(err => {
            $('#hyplan-export-status').text('Error: ' + err.message)
        })
    })

    // --- Draw Line on Map ---
    $('#hyplan-add-line-btn').on('click', function () {
        drawLineMode = true
        drawLineStart = null
        $('#hyplan-add-line-btn').hide()
        $('#hyplan-cancel-draw-btn').show()
        $('#hyplan-add-line-status').text('Click the first point on the map.')
        Map_.map.on('click', onDrawLineClick)
    })

    $('#hyplan-cancel-draw-btn').on('click', function () {
        drawLineMode = false
        drawLineStart = null
        Map_.map.off('click', onDrawLineClick)
        $('#hyplan-cancel-draw-btn').hide()
        $('#hyplan-add-line-btn').show()
        $('#hyplan-add-line-status').text('')
    })

    function onDrawLineClick(e) {
        if (!drawLineMode) return
        if (!drawLineStart) {
            drawLineStart = e.latlng
            $('#hyplan-add-line-status').text(`Start: (${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)}). Click end point.`)
            return
        }
        // Second click — create line
        const altitude = parseFloat($('#hyplan-altitude').val()) || 3000

        fetch(`${SERVICE_URL}/add-line`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId,
                lat1: drawLineStart.lat,
                lon1: drawLineStart.lng,
                lat2: e.latlng.lat,
                lon2: e.latlng.lng,
                altitude_msl_m: altitude,
                site_name: `Line ${$('.hyplan-line-item').length + 1}`,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.detail || data.message) {
                $('#hyplan-add-line-status').text('Error: ' + getErrorMessage(data))
                return
            }
            if (data.campaign_id) campaignId = data.campaign_id
            displayFlightLines(data.flight_lines)
            updateLineList(data.flight_lines)
            $('#hyplan-add-line-status').text(`Added line ${data.added_line_id}.`)
        })
        .catch(err => {
            $('#hyplan-add-line-status').text('Error: ' + err.message)
        })

        drawLineMode = false
        drawLineStart = null
        Map_.map.off('click', onDrawLineClick)
        $('#hyplan-cancel-draw-btn').hide()
        $('#hyplan-add-line-btn').show()
    }

    // --- Relative-to calculator (Section 2b) ----------------------------
    // Wraps /resolve-relative.  Two flows: (a) compute and display the
    // resolved point for copy/paste; (b) if the user has clicked the
    // first endpoint of a line (drawLineStart set), offer to use the
    // resolved point as the line's second endpoint via /add-line.

    let _relComputed = null   // most recent {latitude, longitude}

    $('#hyplan-rel-compute-btn').on('click', function () {
        const anchorLat = parseFloat($('#hyplan-rel-anchor-lat').val())
        const anchorLon = parseFloat($('#hyplan-rel-anchor-lon').val())
        const bearing = parseFloat($('#hyplan-rel-bearing').val()) || 0
        const distance = parseFloat($('#hyplan-rel-distance').val()) || 0
        if (isNaN(anchorLat) || isNaN(anchorLon)) {
            $('#hyplan-rel-status').text('Enter an anchor lat/lon first.')
            return
        }
        $('#hyplan-rel-status').text('Computing...')
        fetch(`${SERVICE_URL}/resolve-relative`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                anchor_lat: anchorLat,
                anchor_lon: anchorLon,
                bearing_deg: bearing,
                distance_m: distance,
            }),
        })
            .then(r => r.json())
            .then(data => {
                if (data.detail) {
                    $('#hyplan-rel-status').text('Failed: ' + getErrorMessage(data))
                    return
                }
                _relComputed = { latitude: data.latitude, longitude: data.longitude }
                $('#hyplan-rel-status').text(
                    `Resolved: (${data.latitude.toFixed(6)}, ${data.longitude.toFixed(6)})`
                )
                // Only offer "use as endpoint" if the user has already
                // clicked a first endpoint on the map.
                if (drawLineStart) {
                    $('#hyplan-rel-use-as-end-btn').show()
                } else {
                    $('#hyplan-rel-use-as-end-btn').hide()
                }
            })
            .catch(err => {
                $('#hyplan-rel-status').text('Error: ' + err.message)
            })
    })

    $('#hyplan-rel-use-as-end-btn').on('click', function () {
        if (!_relComputed || !drawLineStart) return
        const altitude = parseFloat($('#hyplan-altitude').val()) || 3000
        fetch(`${SERVICE_URL}/add-line`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId,
                lat1: drawLineStart.lat,
                lon1: drawLineStart.lng,
                lat2: _relComputed.latitude,
                lon2: _relComputed.longitude,
                altitude_msl_m: altitude,
                site_name: `Line ${$('.hyplan-line-item').length + 1} (rel)`,
            }),
        })
            .then(r => r.json())
            .then(data => {
                if (data.detail || data.message) {
                    $('#hyplan-rel-status').text('Error: ' + getErrorMessage(data))
                    return
                }
                if (data.campaign_id) campaignId = data.campaign_id
                displayFlightLines(data.flight_lines)
                updateLineList(data.flight_lines)
                $('#hyplan-rel-status').text(`Added line ${data.added_line_id} (relative endpoint).`)
                // Exit draw mode.
                drawLineMode = false
                drawLineStart = null
                Map_.map.off('click', onDrawLineClick)
                $('#hyplan-cancel-draw-btn').hide()
                $('#hyplan-add-line-btn').show()
                $('#hyplan-rel-use-as-end-btn').hide()
            })
            .catch(err => {
                $('#hyplan-rel-status').text('Error: ' + err.message)
            })
    })

    // --- Delete Selected Line ---
    $('#hyplan-delete-line-btn').on('click', function () {
        if (!campaignId || selectedLineIds.length === 0) return

        const lineId = selectedLineIds[selectedLineIds.length - 1]
        fetch(`${SERVICE_URL}/delete-line`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId,
                line_id: lineId,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.detail || data.message) {
                $('#hyplan-add-line-status').text('Error: ' + getErrorMessage(data))
                return
            }
            selectedLineIds = selectedLineIds.filter(id => id !== lineId)
            displayFlightLines(data.flight_lines)
            updateLineList(data.flight_lines)
            updateSelectionStatus()
            $('#hyplan-add-line-status').text(`Deleted ${lineId}.`)
        })
        .catch(err => {
            $('#hyplan-add-line-status').text('Error: ' + err.message)
        })
    })

    // --- Transform UI ---
    $('#hyplan-transform-op').on('change', function () {
        const op = $(this).val()
        const label1 = $('#hyplan-transform-label-1')
        const wrap2 = $('#hyplan-transform-val-2-wrap')
        const label2 = $('#hyplan-transform-label-2')

        if (op === 'rotate') {
            label1.text('Angle (deg)')
            wrap2.hide()
        } else if (op === 'offset_across') {
            label1.text('Distance (m, + = right)')
            wrap2.hide()
        } else if (op === 'offset_along') {
            label1.text('Start offset (m)')
            label2.text('End offset (m)')
            wrap2.show()
        } else if (op === 'offset_north_east') {
            label1.text('North (m)')
            label2.text('East (m)')
            wrap2.show()
        } else if (op === 'reverse') {
            label1.text('')
            $('#hyplan-transform-params').hide()
        }
        if (op !== 'reverse') {
            $('#hyplan-transform-params').show()
        }
    })

    $('#hyplan-transform-btn').on('click', function () {
        if (!campaignId || selectedLineIds.length === 0) {
            $('#hyplan-transform-status').text('Select lines first.')
            return
        }

        const op = $('#hyplan-transform-op').val()
        const val1 = parseFloat($('#hyplan-transform-val-1').val()) || 0
        const val2 = parseFloat($('#hyplan-transform-val-2').val()) || 0

        let params = {}
        if (op === 'rotate') params = { angle_deg: val1 }
        else if (op === 'offset_across') params = { distance_m: val1 }
        else if (op === 'offset_along') params = { start_m: val1, end_m: val2 }
        else if (op === 'offset_north_east') params = { north_m: val1, east_m: val2 }
        else if (op === 'reverse') params = {}

        $('#hyplan-transform-status').text('Applying...')
        $('#hyplan-transform-btn').prop('disabled', true)

        fetch(`${SERVICE_URL}/transform-lines`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId,
                line_ids: selectedLineIds,
                operation: op,
                params: params,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.detail || data.message) {
                $('#hyplan-transform-status').text('Error: ' + getErrorMessage(data))
                return
            }
            displayFlightLines(data.flight_lines)
            updateLineList(data.flight_lines)
            $('#hyplan-transform-status').text(`Transformed ${data.transformed} line(s).`)
        })
        .catch(err => {
            $('#hyplan-transform-status').text('Error: ' + err.message)
        })
        .finally(() => {
            $('#hyplan-transform-btn').prop('disabled', false)
        })
    })

    // --- Flight Patterns ---
    // --- Pattern parameter fields ---
    const patternFields = {
        racetrack: [
            { id: 'pp-leg-length', label: 'Leg Length (m)', value: 10000 },
            { id: 'pp-n-legs', label: 'Number of Legs', value: 2 },
            { id: 'pp-offset', label: 'Leg Spacing (m)', value: 1000 },
        ],
        rosette: [
            { id: 'pp-radius', label: 'Radius (m)', value: 5000 },
            { id: 'pp-n-lines', label: 'Number of Lines', value: 3 },
        ],
        polygon: [
            { id: 'pp-radius', label: 'Radius (m)', value: 5000 },
            { id: 'pp-n-sides', label: 'Number of Sides', value: 4 },
            { id: 'pp-aspect-ratio', label: 'Aspect Ratio', value: 1.0 },
        ],
        sawtooth: [
            { id: 'pp-leg-length', label: 'Leg Length (m)', value: 10000 },
            { id: 'pp-alt-min', label: 'Altitude Min (m)', value: 1000 },
            { id: 'pp-alt-max', label: 'Altitude Max (m)', value: 3000 },
            { id: 'pp-n-cycles', label: 'Number of Cycles', value: 2 },
        ],
        spiral: [
            { id: 'pp-radius', label: 'Radius (m)', value: 3000 },
            { id: 'pp-alt-start', label: 'Altitude Start (m)', value: 500 },
            { id: 'pp-alt-end', label: 'Altitude End (m)', value: 3000 },
            { id: 'pp-n-turns', label: 'Number of Turns', value: 3 },
            { id: 'pp-direction', label: 'Direction (right/left)', value: 'right', type: 'text' },
        ],
        glint_arc: [
            { id: 'pp-bank-angle', label: 'Bank Angle (deg, blank = auto)', value: '' },
            { id: 'pp-bank-direction', label: 'Bank Direction (right/left)', value: 'right', type: 'text' },
            { id: 'pp-collection-length', label: 'Collection Length (m, blank = full arc)', value: '' },
        ],
    }

    function renderPatternParams(pattern) {
        const container = $('#hyplan-pattern-params')
        container.empty()
        const fields = patternFields[pattern] || []
        fields.forEach(f => {
            const inputType = f.type || 'number'
            container.append(
                `<label>${f.label}</label>` +
                `<input type="${inputType}" id="${f.id}" value="${f.value}" />`
            )
        })
    }

    renderPatternParams('racetrack')

    $('#hyplan-pattern-type').on('change', function () {
        renderPatternParams($(this).val())
    })

    $('#hyplan-set-pattern-center-btn').on('click', function () {
        patternCenter = null
        $('#hyplan-set-pattern-center-btn').hide()
        $('#hyplan-cancel-pattern-btn').show()
        $('#hyplan-pattern-status').text('Click the map to set the pattern center.')
        Map_.map.on('click', onPatternCenterClick)
    })

    $('#hyplan-cancel-pattern-btn').on('click', function () {
        patternCenter = null
        Map_.map.off('click', onPatternCenterClick)
        $('#hyplan-cancel-pattern-btn').hide()
        $('#hyplan-set-pattern-center-btn').show()
        $('#hyplan-pattern-status').text('')
        $('#hyplan-generate-pattern-btn').prop('disabled', true)
    })

    // Patterns list: delete + include-in-compute checkbox
    $('#hyplan-patterns-list').on('click', '.hyplan-pattern-delete', function () {
        const pid = $(this).data('pid')
        if (pid) deletePattern(pid)
    })
    $('#hyplan-patterns-list').on('change', '.hyplan-pattern-include', function () {
        const pid = $(this).data('pid')
        if (!pid) return
        if (this.checked) {
            if (patternRefsForCompute.indexOf(pid) < 0) patternRefsForCompute.push(pid)
        } else {
            patternRefsForCompute = patternRefsForCompute.filter(id => id !== pid)
        }
    })

    // --- Move Pattern (Section 2e) --------------------------------------
    // Wraps the /transform-pattern endpoint: translate / move_to / rotate
    // a whole pattern in place.  The pattern selector is refreshed from
    // patternsCache by renderMovePatternSelect() whenever the patterns
    // list changes.

    const MOVE_PATTERN_LABELS = {
        translate: ['North (m)', 'East (m)'],
        move_to:   ['Latitude',  'Longitude'],
        rotate:    ['Angle (deg)', null],   // single-value op
    }

    function updateMovePatternForm() {
        const op = $('#hyplan-move-pattern-op').val()
        const [l1, l2] = MOVE_PATTERN_LABELS[op] || ['', null]
        $('#hyplan-move-pattern-label-1').text(l1)
        if (l2 === null) {
            $('#hyplan-move-pattern-val-2-wrap').hide()
        } else {
            $('#hyplan-move-pattern-label-2').text(l2)
            $('#hyplan-move-pattern-val-2-wrap').show()
        }
    }
    updateMovePatternForm()

    $('#hyplan-move-pattern-op').on('change', updateMovePatternForm)

    $('#hyplan-move-pattern-btn').on('click', function () {
        const pid = $('#hyplan-move-pattern-select').val()
        if (!pid || !campaignId) return
        const op = $('#hyplan-move-pattern-op').val()
        const v1 = parseFloat($('#hyplan-move-pattern-val-1').val()) || 0
        const v2 = parseFloat($('#hyplan-move-pattern-val-2').val()) || 0
        let params
        if (op === 'translate')       params = { north_m: v1, east_m: v2 }
        else if (op === 'move_to')    params = { latitude: v1, longitude: v2 }
        else if (op === 'rotate')     params = { angle_deg: v1 }
        else return
        $('#hyplan-move-pattern-status').text('Applying...')
        $('#hyplan-move-pattern-btn').prop('disabled', true)
        fetch(`${SERVICE_URL}/transform-pattern`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId,
                pattern_id: pid,
                operation: op,
                params: params,
            }),
        })
            .then(r => r.json())
            .then(data => {
                if (data.detail) {
                    $('#hyplan-move-pattern-status').text('Failed: ' + getErrorMessage(data))
                    return
                }
                displayFlightLines(data.flight_lines)
                updateLineList(data.flight_lines)
                renderPatternsLayer(data.patterns)
                // Update the cache entry's params so the next op runs
                // against current state.
                const idx = patternsCache.findIndex(p => p.pattern_id === pid)
                if (idx >= 0) patternsCache[idx] = {
                    pattern_id: data.pattern_id,
                    kind: data.pattern_kind,
                    name: data.pattern_name,
                    is_line_based: data.is_line_based,
                    params: data.pattern_params,
                }
                $('#hyplan-move-pattern-status').text(`Applied ${op} to ${data.pattern_name}.`)
            })
            .catch(err => {
                $('#hyplan-move-pattern-status').text('Error: ' + err.message)
            })
            .finally(() => {
                $('#hyplan-move-pattern-btn').prop('disabled', false)
            })
    })

    function onPatternCenterClick(e) {
        patternCenter = e.latlng
        Map_.map.off('click', onPatternCenterClick)
        $('#hyplan-cancel-pattern-btn').hide()
        $('#hyplan-set-pattern-center-btn').show()
        $('#hyplan-generate-pattern-btn').prop('disabled', false)
        $('#hyplan-pattern-status').text(`Center: (${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)})`)
    }

    // --- Section 5: Solar Position --------------------------------------
    $('#hyplan-pick-solar-point-btn').on('click', function () {
        $('#hyplan-pick-solar-point-btn').hide()
        $('#hyplan-cancel-pick-solar-btn').show()
        $('#hyplan-solar-status').text('Click the map to plot solar zenith here.')
        Map_.map.on('click', onSolarPickClick)
    })

    $('#hyplan-cancel-pick-solar-btn').on('click', function () {
        Map_.map.off('click', onSolarPickClick)
        $('#hyplan-cancel-pick-solar-btn').hide()
        $('#hyplan-pick-solar-point-btn').show()
        $('#hyplan-solar-status').text('')
    })

    function onSolarPickClick(e) {
        Map_.map.off('click', onSolarPickClick)
        $('#hyplan-cancel-pick-solar-btn').hide()
        $('#hyplan-pick-solar-point-btn').show()
        showSolarMarker(e.latlng.lat, e.latlng.lng)
        requestAndRenderSolar(e.latlng.lat, e.latlng.lng)
    }

    $('#hyplan-solar-from-line-btn').on('click', function () {
        if (selectedLineIds.length === 0) {
            $('#hyplan-solar-status').text('Select a flight line first.')
            return
        }
        const targetId = selectedLineIds[0]
        const mid = midpointOfFlightLine(targetId)
        if (!mid) {
            $('#hyplan-solar-status').text(`Could not locate line '${targetId}' on the map.`)
            return
        }
        showSolarMarker(mid.lat, mid.lon)
        requestAndRenderSolar(mid.lat, mid.lon)
    })

    $('#hyplan-hide-solar-btn').on('click', function () {
        hyplanDisownAndRemove(solarMarkerLayer)
        solarMarkerLayer = null
        $('#hyplan-solar-plot').empty()
        $('#hyplan-solar-meta').empty()
        $('#hyplan-solar-status').text('')
        $('#hyplan-hide-solar-btn').hide()
    })

    $('#hyplan-generate-pattern-btn').on('click', function () {
        if (!patternCenter) return

        const pattern = $('#hyplan-pattern-type').val()
        const heading = parseFloat($('#hyplan-pattern-heading').val()) || 0
        const altitude = parseFloat($('#hyplan-altitude').val()) || 3000
        const name = $('#hyplan-campaign-name').val() || 'Mission'

        // Read dynamic pattern parameters
        const pp = {}
        const fields = patternFields[pattern] || []
        fields.forEach(f => {
            const raw = $(`#${f.id}`).val()
            pp[f.id] = f.type === 'text' ? raw : (parseFloat(raw) || f.value)
        })

        // Map UI field IDs to service param names
        const params = {}
        if (pp['pp-leg-length'] !== undefined) params.leg_length_m = pp['pp-leg-length']
        if (pp['pp-radius'] !== undefined) params.radius_m = pp['pp-radius']
        if (pp['pp-n-legs'] !== undefined) params.n_legs = pp['pp-n-legs']
        if (pp['pp-offset'] !== undefined) params.offset_m = pp['pp-offset']
        if (pp['pp-n-lines'] !== undefined) params.n_lines = pp['pp-n-lines']
        if (pp['pp-n-sides'] !== undefined) params.n_sides = pp['pp-n-sides']
        if (pp['pp-aspect-ratio'] !== undefined) params.aspect_ratio = pp['pp-aspect-ratio']
        if (pp['pp-alt-min'] !== undefined) params.altitude_min_m = pp['pp-alt-min']
        if (pp['pp-alt-max'] !== undefined) params.altitude_max_m = pp['pp-alt-max']
        if (pp['pp-n-cycles'] !== undefined) params.n_cycles = pp['pp-n-cycles']
        if (pp['pp-alt-start'] !== undefined) params.altitude_start_m = pp['pp-alt-start']
        if (pp['pp-alt-end'] !== undefined) params.altitude_end_m = pp['pp-alt-end']
        if (pp['pp-n-turns'] !== undefined) params.n_turns = pp['pp-n-turns']
        if (pp['pp-direction'] !== undefined) params.direction = pp['pp-direction']
        // glint_arc params
        if (pp['pp-bank-angle'] !== undefined && pp['pp-bank-angle'] !== '') {
            params.bank_angle = pp['pp-bank-angle']
        }
        if (pp['pp-bank-direction'] !== undefined) params.bank_direction = pp['pp-bank-direction']
        if (pp['pp-collection-length'] !== undefined && pp['pp-collection-length'] !== '') {
            params.collection_length_m = pp['pp-collection-length']
        }

        // takeoff_time + aircraft only required by glint_arc; harmless to send always
        const takeoffTime = getTakeoffTimeUtcIso()
        const aircraftSel = $('#hyplan-aircraft').val() || null

        const bounds = Map_.map.getBounds()
        const campaignBounds = [
            bounds.getWest(), bounds.getSouth(),
            bounds.getEast(), bounds.getNorth(),
        ]

        $('#hyplan-pattern-status').text('Generating...')
        $('#hyplan-generate-pattern-btn').prop('disabled', true)

        fetch(`${SERVICE_URL}/generate-pattern`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId || 'campaign-' + Date.now(),
                campaign_name: name,
                campaign_bounds: campaignBounds,
                pattern: pattern,
                center_lat: patternCenter.lat,
                center_lon: patternCenter.lng,
                heading: heading,
                altitude_msl_m: altitude,
                params: params,
                takeoff_time: takeoffTime,
                aircraft: aircraftSel,
                sensor: $('#hyplan-sensor').val() || null,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.detail || data.message) {
                $('#hyplan-pattern-status').text('Error: ' + getErrorMessage(data))
                return
            }
            campaignId = data.campaign_id

            // Refresh flight lines (line-based pattern legs are included).
            if (data.flight_lines) {
                displayFlightLines(data.flight_lines)
                updateLineList(data.flight_lines)
            }

            // Refresh the patterns map layer + UI list.
            if (data.patterns) {
                renderPatternsLayer(data.patterns)
            }

            // Auto-include newly-generated waypoint patterns in compute.
            if (data.is_line_based === false && data.pattern_id) {
                if (patternRefsForCompute.indexOf(data.pattern_id) < 0) {
                    patternRefsForCompute.push(data.pattern_id)
                }
            }

            // Refresh the patterns list. Use the new pattern's metadata
            // until the next /patterns fetch.
            patternsCache.push({
                pattern_id: data.pattern_id,
                kind: data.pattern_kind,
                name: data.pattern_name,
                is_line_based: data.is_line_based,
            })
            renderPatternsList()

            // Glint arc preview: swath polygon + colored sample dots
            renderGlintArcPreview(data)

            const label = data.is_line_based ? 'flight lines' : 'waypoints'
            let status = `Generated ${data.pattern_name} (${data.pattern_kind}). Added to campaign as ${label}.`
            if (data.arc_glint_summary) {
                const s = data.arc_glint_summary
                status += ` Mean glint ${s.mean_glint.toFixed(1)}°, min ${s.min_glint.toFixed(1)}°, ${(s.fraction_below_threshold * 100).toFixed(0)}% < ${s.threshold_deg}°.`
            }
            $('#hyplan-pattern-status').text(status)
        })
        .catch(err => {
            $('#hyplan-pattern-status').text('Error: ' + err.message)
        })
        .finally(() => {
            $('#hyplan-generate-pattern-btn').prop('disabled', false)
        })
    })

    // --- Box Select (Shift+Drag) ---
    let boxSelectRect = null
    let boxSelectStart = null

    function onBoxSelectMouseDown(e) {
        if (!e.originalEvent.shiftKey) return
        boxSelectStart = e.latlng
        Map_.map.dragging.disable()
    }

    function onBoxSelectMouseMove(e) {
        if (!boxSelectStart) return
        if (boxSelectRect) Map_.map.removeLayer(boxSelectRect)
        boxSelectRect = window.L.rectangle(
            [boxSelectStart, e.latlng],
            { color: '#60a5fa', weight: 2, fillOpacity: 0.15, dashArray: '5 5' }
        ).addTo(Map_.map)
    }

    function onBoxSelectMouseUp(e) {
        if (!boxSelectStart) return
        Map_.map.dragging.enable()

        const bounds = window.L.latLngBounds(boxSelectStart, e.latlng)
        boxSelectStart = null
        if (boxSelectRect) {
            Map_.map.removeLayer(boxSelectRect)
            boxSelectRect = null
        }

        // Select all flight lines that intersect the box
        if (flightLineLayer) {
            flightLineLayer.eachLayer(function (layer) {
                const lineId = layer.feature.properties.line_id || layer.feature.id
                const coords = layer.getLatLngs()
                let intersects = false
                for (let i = 0; i < coords.length; i++) {
                    if (bounds.contains(coords[i])) {
                        intersects = true
                        break
                    }
                }
                if (intersects && selectedLineIds.indexOf(lineId) < 0) {
                    selectedLineIds.push(lineId)
                    $(`.hyplan-line-item[data-lineid="${lineId}"]`).addClass('selected')
                    layer.setStyle({ color: '#1d4ed8', weight: 4, opacity: 1.0 })
                }
            })
            updateSelectionStatus()
        }
    }

    Map_.map.on('mousedown', onBoxSelectMouseDown)
    Map_.map.on('mousemove', onBoxSelectMouseMove)
    Map_.map.on('mouseup', onBoxSelectMouseUp)

    function separateFromMMGIS() {
        Map_.map.off('mousedown', onBoxSelectMouseDown)
        Map_.map.off('mousemove', onBoxSelectMouseMove)
        Map_.map.off('mouseup', onBoxSelectMouseUp)
        if (boxSelectRect) {
            Map_.map.removeLayer(boxSelectRect)
            boxSelectRect = null
        }
        hyplanDisownAndRemove(flightLineLayer); flightLineLayer = null
        hyplanDisownAndRemove(planLayer); planLayer = null
        hyplanDisownAndRemove(windLayer); windLayer = null
        hyplanDisownAndRemove(patternsLayer); patternsLayer = null
        hyplanDisownAndRemove(swathLayer); swathLayer = null
        hyplanDisownAndRemove(glintLayer); glintLayer = null
        hyplanDisownAndRemove(glintArcLayer); glintArcLayer = null
        hyplanDisownAndRemove(solarMarkerLayer); solarMarkerLayer = null
        campaignId = null
        selectedLineIds = []
    }
}

// --- Shared UI / rendering helpers -----------------------------------------

function getTakeoffTimeUtcIso() {
    return parseLocalDateTimeToUtcIso($('#hyplan-takeoff-time').val())
}

function updateTakeoffTimeMeta() {
    const $meta = $('#hyplan-takeoff-time-meta')
    if ($meta.length === 0) return

    const raw = ($('#hyplan-takeoff-time').val() || '').trim()
    if (!raw) {
        $meta.text('')
        return
    }

    const date = new Date(raw)
    if (Number.isNaN(date.getTime())) {
        $meta.text('Invalid local date/time.')
        return
    }

    const utcIso = date.toISOString().replace('.000Z', 'Z')
    const utcDisplay = utcIso.slice(0, 16).replace('T', ' ')
    $meta.text(`Sent as ${utcDisplay} UTC (local UTC${formatUtcOffset(date)}).`)
}

function getDrawnPolygon() {
    // Try to get the most recently drawn polygon from the Draw tool's layers
    let polygon = null
    Map_.map.eachLayer(function (layer) {
        if (layer.feature && layer.feature.geometry &&
            (layer.feature.geometry.type === 'Polygon' ||
             layer.feature.geometry.type === 'MultiPolygon')) {
            polygon = layer.feature
        }
        // Also check for Leaflet drawn polygons
        if (layer instanceof window.L.Polygon && !(layer instanceof window.L.Rectangle) && layer.toGeoJSON) {
            polygon = layer.toGeoJSON()
        }
        if (layer instanceof window.L.Rectangle && layer.toGeoJSON) {
            polygon = layer.toGeoJSON()
        }
    })
    return polygon
}

function displayFlightLines(geojson) {
    hyplanDisownAndRemove(flightLineLayer)
    flightLineLayer = hyplanOwn(window.L.geoJSON(geojson, {
        interactive: true,
        style: {
            color: '#3b82f6',
            weight: 4,
            opacity: 0.8,
        },
        onEachFeature: function (feature, layer) {
            const name = feature.properties.site_name || feature.properties.line_id
            layer.bindTooltip(name, { sticky: true })
            layer.on('click', function () {
                const lineId = feature.properties.line_id || feature.id
                toggleLineSelection(lineId)
            })
            // Right-click on a flight line surfaces the same per-line
            // ops we have in Section 3b (Transform Selected Lines)
            // and Section 2b (delete), without making the user open
            // those sections and click the line in the list first.
            layer.on('contextmenu', function (e) {
                const lineId = feature.properties.line_id || feature.id
                window.L.DomEvent.stopPropagation(e)
                if (e.originalEvent) e.originalEvent.preventDefault()
                showLineContextMenu(lineId, name, e.originalEvent)
            })
        },
    })).addTo(Map_.map)

    // Zoom map to show all lines
    try {
        const layerBounds = flightLineLayer.getBounds()
        if (layerBounds.isValid()) {
            Map_.map.fitBounds(layerBounds, { padding: [20, 20] })
        }
    } catch (e) { /* ignore */ }
}

function displayPlan(geojson) {
    hyplanDisownAndRemove(planLayer)
    const segmentColors = {
        takeoff: '#ef4444',
        climb: '#f97316',
        transit: '#6b7280',
        descent: '#3b82f6',
        flight_line: '#22c55e',
        approach: '#8b5cf6',
    }
    planLayer = hyplanOwn(window.L.geoJSON(geojson, {
        style: function (feature) {
            const segType = feature.properties.segment_type || 'transit'
            return {
                color: segmentColors[segType] || '#6b7280',
                weight: 3,
                opacity: 0.9,
                dashArray: segType === 'transit' ? '5 5' : null,
            }
        },
        onEachFeature: function (feature, layer) {
            const p = feature.properties
            layer.bindTooltip(
                `${p.segment_type}: ${p.segment_name || ''}`,
                { sticky: true }
            )
        },
    })).addTo(Map_.map)
}

function updateLineList(geojson) {
    const list = $('#hyplan-line-list')
    list.empty()
    selectedLineIds = []

    geojson.features.forEach(function (f) {
        const lineId = f.properties.line_id || f.id
        const name = f.properties.site_name || lineId
        // Compute length from coordinates
        const coords = f.geometry.coordinates
        let lengthKm = ''
        if (coords && coords.length >= 2) {
            const dLat = (coords[1][1] - coords[0][1]) * 111.32
            const dLon = (coords[1][0] - coords[0][0]) * 111.32 * Math.cos(coords[0][1] * Math.PI / 180)
            lengthKm = Math.sqrt(dLat * dLat + dLon * dLon).toFixed(1)
        }
        const alt = f.properties.altitude_msl ? `${f.properties.altitude_msl}m` : ''

        const row = $('<div>').addClass('hyplan-line-item').attr('data-lineid', lineId)

        const label = $('<span>')
            .css('flex', '1')
            .html(`${name} <span style="color:#888; font-size:10px">${lengthKm}km ${alt}</span>`)
            .on('click', function () { toggleLineSelection(lineId) })

        const upBtn = $('<span>')
            .html('&#9650;')
            .css({ cursor: 'pointer', padding: '0 3px', fontSize: '10px' })
            .attr('title', 'Move up')
            .on('click', function (e) { e.stopPropagation(); moveLineInSelection(lineId, -1) })

        const downBtn = $('<span>')
            .html('&#9660;')
            .css({ cursor: 'pointer', padding: '0 3px', fontSize: '10px' })
            .attr('title', 'Move down')
            .on('click', function (e) { e.stopPropagation(); moveLineInSelection(lineId, 1) })

        row.css({ display: 'flex', alignItems: 'center' })
        row.append(label, upBtn, downBtn)
        list.append(row)
    })
}

function moveLineInSelection(lineId, direction) {
    const idx = selectedLineIds.indexOf(lineId)
    if (idx < 0) return
    const newIdx = idx + direction
    if (newIdx < 0 || newIdx >= selectedLineIds.length) return
    // Swap
    const temp = selectedLineIds[newIdx]
    selectedLineIds[newIdx] = selectedLineIds[idx]
    selectedLineIds[idx] = temp
    // Reorder the DOM to match
    const list = $('#hyplan-line-list')
    selectedLineIds.forEach(lid => {
        const item = $(`.hyplan-line-item[data-lineid="${lid}"]`)
        list.append(item)
    })
    // Move unselected items to the end
    $('.hyplan-line-item').each(function () {
        if (selectedLineIds.indexOf($(this).data('lineid')) < 0) {
            list.append($(this))
        }
    })
}

function toggleLineSelection(lineId) {
    const item = $(`.hyplan-line-item[data-lineid="${lineId}"]`)
    const idx = selectedLineIds.indexOf(lineId)
    if (idx >= 0) {
        selectedLineIds.splice(idx, 1)
        item.removeClass('selected')
    } else {
        selectedLineIds.push(lineId)
        item.addClass('selected')
    }
    updateSelectionStatus()

    // Update line colors on map
    if (flightLineLayer) {
        flightLineLayer.eachLayer(function (layer) {
            const fLineId = layer.feature.properties.line_id || layer.feature.id
            if (selectedLineIds.indexOf(fLineId) >= 0) {
                layer.setStyle({ color: '#1d4ed8', weight: 4, opacity: 1.0 })
            } else {
                layer.setStyle({ color: '#3b82f6', weight: 2, opacity: 0.8 })
            }
        })
    }
}

function updateSelectionStatus() {
    const total = $('.hyplan-line-item').length
    const selected = selectedLineIds.length
    $('#hyplan-selection-status').text(`${selected} of ${total} selected`)
    $('#hyplan-compute-btn').prop('disabled', selected === 0 && patternRefsForCompute.length === 0)
    $('#hyplan-optimize-btn').prop('disabled', selected < 2)
    $('#hyplan-delete-line-btn').prop('disabled', selected === 0)
    $('#hyplan-transform-btn').prop('disabled', selected === 0)
    $('#hyplan-show-swaths-btn').prop('disabled', selected === 0)
    $('#hyplan-show-glint-btn').prop('disabled', selected === 0)
    $('#hyplan-solar-from-line-btn').prop('disabled', selected === 0)
}

// Analysis rendering helpers: glint overlays and pattern decoration that sit
// alongside the core line/plan layers.  Pure helpers (getErrorMessage,
// glintColor, formatUtcOffset, parseLocalDateTimeToUtcIso) live in
// helpers.js so they can be unit-tested without the MMGIS host.

function renderGlintSummary(summary, thresholdDeg) {
    const $box = $('#hyplan-glint-summary')
    $box.empty()
    if (!summary || summary.length === 0) return
    const fmt = v => (v == null ? '—' : Number(v).toFixed(1))
    const fmtPct = v => (v == null ? '—' : (Number(v) * 100).toFixed(1) + '%')
    const sunFlag = z => (z != null && z > 90 ? ' ⚠' : '')
    const header = `<div class="hyplan-glint-row hyplan-glint-header">
        <span class="hyplan-glint-name">Line</span>
        <span class="hyplan-glint-num">sun°</span>
        <span class="hyplan-glint-num">mean</span>
        <span class="hyplan-glint-num">min</span>
        <span class="hyplan-glint-num">&lt; ${thresholdDeg}°</span>
    </div>`
    $box.append(header)
    summary.forEach(function (s) {
        const row = `<div class="hyplan-glint-row">
            <span class="hyplan-glint-name" title="${s.line_id}">${s.site_name || s.line_id}</span>
            <span class="hyplan-glint-num">${fmt(s.solar_zenith)}°${sunFlag(s.solar_zenith)}</span>
            <span class="hyplan-glint-num">${fmt(s.mean_glint)}°</span>
            <span class="hyplan-glint-num">${fmt(s.min_glint)}°</span>
            <span class="hyplan-glint-num">${fmtPct(s.fraction_below_threshold)}</span>
        </div>`
        $box.append(row)
    })
}

function renderPatternsLayer(geojson) {
    // HyPlan's patterns_to_geojson() emits features only for waypoint-based
    // patterns (polygon, sawtooth, spiral). Line-based patterns (rosette,
    // racetrack) are rendered via the flight_lines layer, so nothing shows up
    // here for them.
    hyplanDisownAndRemove(patternsLayer)
    patternsLayer = null
    if (!geojson || !geojson.features || geojson.features.length === 0) return
    patternsLayer = hyplanOwn(window.L.geoJSON(geojson, {
        style: function () {
            return { color: '#f59e0b', weight: 2, opacity: 0.85, dashArray: '4 4' }
        },
        pointToLayer: function (feature, latlng) {
            return window.L.circleMarker(latlng, {
                radius: 4, fillColor: '#f59e0b', color: '#fff',
                weight: 1, opacity: 1, fillOpacity: 0.85,
            })
        },
        onEachFeature: function (feature, layer) {
            const p = feature.properties || {}
            const label = p.name || p.pattern_kind
            if (label) layer.bindTooltip(label, { sticky: true })
            // Right-click on a pattern: same operations as Section
            // 2e (Move Pattern) and 2d (Delete), available in place.
            layer.on('contextmenu', function (e) {
                const pid = p.pattern_id
                if (!pid) return
                window.L.DomEvent.stopPropagation(e)
                if (e.originalEvent) e.originalEvent.preventDefault()
                showPatternContextMenu(pid, label || pid, e.originalEvent)
            })
        },
    })).addTo(Map_.map)
}

// --- Right-click context menus ------------------------------------------
// One menu shown at a time; clicking elsewhere or pressing Escape
// dismisses.  Items hit the same backend endpoints the panel sections
// do, just bypassing the section-and-click-in-list dance.

let _hyplanContextMenu = null

function dismissContextMenu() {
    if (_hyplanContextMenu && _hyplanContextMenu.parentNode) {
        _hyplanContextMenu.parentNode.removeChild(_hyplanContextMenu)
    }
    _hyplanContextMenu = null
    document.removeEventListener('mousedown', _dismissOnOutsideClick, true)
    document.removeEventListener('keydown', _dismissOnEscape, true)
}
function _dismissOnOutsideClick(e) {
    if (_hyplanContextMenu && !_hyplanContextMenu.contains(e.target)) {
        dismissContextMenu()
    }
}
function _dismissOnEscape(e) {
    if (e.key === 'Escape') dismissContextMenu()
}

function showContextMenu(originalEvent, headerText, items) {
    // items: [{ label, danger?: bool, onClick: () => void }] or
    //         { divider: true }
    dismissContextMenu()
    const menu = document.createElement('div')
    menu.className = 'hyplan-context-menu'

    if (headerText) {
        const h = document.createElement('div')
        h.className = 'hyplan-cm-header'
        h.textContent = headerText
        menu.appendChild(h)
    }

    items.forEach(function (it) {
        if (it.divider) {
            const d = document.createElement('div')
            d.className = 'hyplan-cm-divider'
            menu.appendChild(d)
            return
        }
        const btn = document.createElement('button')
        btn.className = 'hyplan-cm-item' + (it.danger ? ' danger' : '')
        btn.textContent = it.label
        btn.addEventListener('click', function () {
            dismissContextMenu()
            try {
                it.onClick()
            } catch (e) { /* surface in JS console; panel status will catch follow-on errors */ }
        })
        menu.appendChild(btn)
    })

    // Position at the cursor; flip if it would overflow the viewport edge.
    document.body.appendChild(menu)
    const r = menu.getBoundingClientRect()
    let x = originalEvent.pageX
    let y = originalEvent.pageY
    if (x + r.width > window.innerWidth) x = window.innerWidth - r.width - 4
    if (y + r.height > window.innerHeight) y = window.innerHeight - r.height - 4
    menu.style.left = x + 'px'
    menu.style.top = y + 'px'

    _hyplanContextMenu = menu
    // Defer attaching the dismiss listeners so the click that opened
    // the menu doesn't immediately close it.
    setTimeout(function () {
        document.addEventListener('mousedown', _dismissOnOutsideClick, true)
        document.addEventListener('keydown', _dismissOnEscape, true)
    }, 0)
}

function showLineContextMenu(lineId, siteName, originalEvent) {
    if (!campaignId) return
    const items = [
        { label: 'Reverse direction',     onClick: () => transformOneLine(lineId, 'reverse', {}) },
        { label: 'Rotate +15°',           onClick: () => transformOneLine(lineId, 'rotate', { angle_deg: 15 }) },
        { label: 'Rotate −15°',           onClick: () => transformOneLine(lineId, 'rotate', { angle_deg: -15 }) },
        { label: 'Offset across +500 m',  onClick: () => transformOneLine(lineId, 'offset_across', { distance_m: 500 }) },
        { label: 'Offset across −500 m',  onClick: () => transformOneLine(lineId, 'offset_across', { distance_m: -500 }) },
        { label: 'Shift N 1 km',          onClick: () => transformOneLine(lineId, 'offset_north_east', { north_m: 1000, east_m: 0 }) },
        { label: 'Shift E 1 km',          onClick: () => transformOneLine(lineId, 'offset_north_east', { north_m: 0, east_m: 1000 }) },
        { divider: true },
        { label: 'Delete line',           danger: true, onClick: () => deleteOneLine(lineId) },
    ]
    showContextMenu(originalEvent, siteName + ' (line)', items)
}

function showPatternContextMenu(patternId, name, originalEvent) {
    if (!campaignId) return
    const items = [
        { label: 'Translate N 1 km',  onClick: () => transformOnePattern(patternId, 'translate', { north_m: 1000, east_m: 0 }) },
        { label: 'Translate S 1 km',  onClick: () => transformOnePattern(patternId, 'translate', { north_m: -1000, east_m: 0 }) },
        { label: 'Translate E 1 km',  onClick: () => transformOnePattern(patternId, 'translate', { north_m: 0, east_m: 1000 }) },
        { label: 'Translate W 1 km',  onClick: () => transformOnePattern(patternId, 'translate', { north_m: 0, east_m: -1000 }) },
        { label: 'Rotate +15°',       onClick: () => transformOnePattern(patternId, 'rotate', { angle_deg: 15 }) },
        { label: 'Rotate −15°',       onClick: () => transformOnePattern(patternId, 'rotate', { angle_deg: -15 }) },
        { divider: true },
        { label: 'Delete pattern',    danger: true, onClick: () => deletePattern(patternId) },
    ]
    showContextMenu(originalEvent, name + ' (pattern)', items)
}

function transformOneLine(lineId, operation, params) {
    fetch(`${SERVICE_URL}/transform-lines`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            campaign_id: campaignId,
            line_ids: [lineId],
            operation: operation,
            params: params,
        }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.detail) return
            displayFlightLines(data.flight_lines)
            updateLineList(data.flight_lines)
            renderPatternsLayer(data.patterns)
        })
}

function deleteOneLine(lineId) {
    fetch(`${SERVICE_URL}/delete-line`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign_id: campaignId, line_id: lineId }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.detail) return
            displayFlightLines(data.flight_lines)
            updateLineList(data.flight_lines)
            renderPatternsLayer(data.patterns)
            selectedLineIds = selectedLineIds.filter(id => id !== lineId)
        })
}

function transformOnePattern(patternId, operation, params) {
    fetch(`${SERVICE_URL}/transform-pattern`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            campaign_id: campaignId,
            pattern_id: patternId,
            operation: operation,
            params: params,
        }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.detail) return
            displayFlightLines(data.flight_lines)
            updateLineList(data.flight_lines)
            renderPatternsLayer(data.patterns)
            const idx = patternsCache.findIndex(p => p.pattern_id === patternId)
            if (idx >= 0) patternsCache[idx] = {
                pattern_id: data.pattern_id,
                kind: data.pattern_kind,
                name: data.pattern_name,
                is_line_based: data.is_line_based,
                params: data.pattern_params,
            }
        })
}

function renderPatternsList() {
    const $list = $('#hyplan-patterns-list')
    $list.empty()
    if (patternsCache.length === 0) {
        $list.append('<p class="hyplan-empty">No patterns in this campaign.</p>')
        renderMovePatternSelect()
        return
    }
    patternsCache.forEach(function (pat) {
        const inCompute = patternRefsForCompute.indexOf(pat.pattern_id) >= 0
        const checkbox = pat.is_line_based
            ? ''  // line-based legs included via line selection
            : `<input type="checkbox" class="hyplan-pattern-include" data-pid="${pat.pattern_id}" ${inCompute ? 'checked' : ''} title="Include in compute" />`
        const row = $(
            `<div class="hyplan-pattern-item" data-pid="${pat.pattern_id}">
                ${checkbox}
                <span class="hyplan-pattern-name">${pat.name} <small>(${pat.kind})</small></span>
                <button class="hyplan-pattern-delete" data-pid="${pat.pattern_id}">Delete</button>
            </div>`
        )
        $list.append(row)
    })
    renderMovePatternSelect()
}

function renderMovePatternSelect() {
    // Mirror patternsCache into the 2e move-pattern dropdown.
    const $sel = $('#hyplan-move-pattern-select')
    const prev = $sel.val()
    $sel.empty()
    if (patternsCache.length === 0) {
        $sel.append('<option value="">(no patterns)</option>')
        $('#hyplan-move-pattern-btn').prop('disabled', true)
        return
    }
    patternsCache.forEach(function (pat) {
        $sel.append(`<option value="${pat.pattern_id}">${pat.name} (${pat.kind})</option>`)
    })
    if (prev && patternsCache.find(p => p.pattern_id === prev)) {
        $sel.val(prev)
    }
    $('#hyplan-move-pattern-btn').prop('disabled', false)
}

function deletePattern(patternId) {
    if (!campaignId) return
    fetch(`${SERVICE_URL}/delete-pattern`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ campaign_id: campaignId, pattern_id: patternId }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.detail) {
                $('#hyplan-pattern-status').text('Delete failed: ' + getErrorMessage(data))
                return
            }
            displayFlightLines(data.flight_lines)
            updateLineList(data.flight_lines)
            renderPatternsLayer(data.patterns)
            patternsCache = patternsCache.filter(p => p.pattern_id !== patternId)
            patternRefsForCompute = patternRefsForCompute.filter(id => id !== patternId)
            renderPatternsList()
            // The arc-glint preview is per-pattern; clear if the deleted one
            // owns the layer (we only track the most recent).
            hyplanDisownAndRemove(glintArcLayer)
            glintArcLayer = null
            $('#hyplan-pattern-status').text(`Deleted pattern ${patternId}.`)
        })
        .catch(err => {
            $('#hyplan-pattern-status').text('Delete error: ' + err.message)
        })
}

function renderGlintArcPreview(data) {
    // Replace the previous arc-glint layer (only one shown at a time).
    hyplanDisownAndRemove(glintArcLayer)
    glintArcLayer = null
    if (!data || (!data.arc_glint && !data.arc_swath)) return

    const layers = []

    if (data.arc_swath) {
        const swath = window.L.geoJSON(data.arc_swath, {
            style: {
                color: '#f59e0b',
                weight: 1.5,
                fillColor: '#f59e0b',
                fillOpacity: 0.10,
            },
            onEachFeature: function (feature, layer) {
                const p = feature.properties || {}
                if (p.name) layer.bindTooltip(`${p.name} swath`, { sticky: true })
            },
        })
        layers.push(swath)
    }

    if (data.arc_glint && data.arc_glint.features && data.arc_glint.features.length > 0) {
        const dots = window.L.geoJSON(data.arc_glint, {
            pointToLayer: function (feature, latlng) {
                const ga = feature.properties.glint_angle
                return window.L.circleMarker(latlng, {
                    radius: 3,
                    stroke: false,
                    fillColor: glintColor(ga),
                    fillOpacity: 0.85,
                    interactive: false,
                })
            },
        })
        layers.push(dots)
    }

    if (layers.length === 0) return
    glintArcLayer = hyplanOwn(window.L.layerGroup(layers))
    glintArcLayer.addTo(Map_.map)
}

// --- Solar position helpers -------------------------------------------------

function showSolarMarker(lat, lon) {
    hyplanDisownAndRemove(solarMarkerLayer)
    solarMarkerLayer = hyplanOwn(window.L.circleMarker([lat, lon], {
        radius: 6,
        color: '#ffffff',
        weight: 2,
        fillColor: '#facc15',
        fillOpacity: 0.95,
    }))
    solarMarkerLayer.addTo(Map_.map)
    solarMarkerLayer.bindTooltip(`Solar plot point (${lat.toFixed(4)}, ${lon.toFixed(4)})`, { sticky: true })
}

function midpointOfFlightLine(lineId) {
    if (!flightLineLayer) return null
    let mid = null
    flightLineLayer.eachLayer(function (layer) {
        const f = layer.feature
        if (!f) return
        const fid = (f.properties && f.properties.line_id) || f.id
        if (fid !== lineId) return
        // Prefer geometry coords for an exact midpoint of the LineString
        try {
            const coords = f.geometry.coordinates
            const a = coords[0], b = coords[coords.length - 1]
            mid = { lat: (a[1] + b[1]) / 2, lon: (a[0] + b[0]) / 2 }
        } catch (e) {
            // Fall back to layer bounds center
            try {
                const c = layer.getBounds().getCenter()
                mid = { lat: c.lat, lon: c.lng }
            } catch (_) { /* leave mid null */ }
        }
    })
    return mid
}

function requestAndRenderSolar(lat, lon) {
    // Date: from takeoff_time if set, else today (UTC)
    const takeoffTimeUtc = getTakeoffTimeUtcIso()
    let date
    if (takeoffTimeUtc && takeoffTimeUtc.length >= 10) {
        date = takeoffTimeUtc.slice(0, 10)
    } else {
        date = new Date().toISOString().slice(0, 10)
    }

    $('#hyplan-solar-status').text('Computing solar positions…')
    $('#hyplan-hide-solar-btn').show()

    fetch(`${SERVICE_URL}/solar-position`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat: lat, lon: lon, date: date }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.detail || data.message) {
            $('#hyplan-solar-status').text('Error: ' + getErrorMessage(data))
            return
        }
        renderSolarPlot(data, takeoffTimeUtc)
        $('#hyplan-solar-status').text(`Plotted ${data.zenith_deg.length} samples for ${data.date}.`)
    })
    .catch(err => {
        $('#hyplan-solar-status').text('Error: ' + err.message)
    })
}

function polygonCentroid(geojsonFeature) {
    // Average of unique vertices (good enough for the small polygons drawn here).
    if (!geojsonFeature || !geojsonFeature.geometry) return null
    const geom = geojsonFeature.geometry
    let rings = []
    if (geom.type === 'Polygon') {
        rings = geom.coordinates  // [[ [lon,lat], ... ]]
    } else if (geom.type === 'MultiPolygon') {
        rings = geom.coordinates[0]  // outer of first poly
    } else {
        return null
    }
    const outer = rings[0] || []
    if (outer.length === 0) return null
    let sumLat = 0, sumLon = 0, n = 0
    // skip the duplicate closing vertex if present
    const last = outer[outer.length - 1]
    const first = outer[0]
    const closed = last && first && last[0] === first[0] && last[1] === first[1]
    const stop = closed ? outer.length - 1 : outer.length
    for (let i = 0; i < stop; i++) {
        sumLon += outer[i][0]
        sumLat += outer[i][1]
        n++
    }
    if (n === 0) return null
    return { lat: sumLat / n, lon: sumLon / n }
}

function renderAzimuthSweepPlot(data) {
    const $box = $('#hyplan-optimize-azimuth-plot')
    $box.empty()

    const W = 300, H = 150
    const PAD_L = 36, PAD_R = 14, PAD_T = 12, PAD_B = 24
    const innerW = W - PAD_L - PAD_R
    const innerH = H - PAD_T - PAD_B
    const xMin = 0, xMax = 360
    const yMin = 0, yMax = 90    // glint angle range to display
    const xToPx = h => PAD_L + ((h - xMin) / (xMax - xMin)) * innerW
    const yToPx = v => PAD_T + (1 - (v - yMin) / (yMax - yMin)) * innerH  // inverted: high glint at top

    const svgNS = 'http://www.w3.org/2000/svg'
    const svg = document.createElementNS(svgNS, 'svg')
    svg.setAttribute('width', W)
    svg.setAttribute('height', H)
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`)
    svg.setAttribute('class', 'hyplan-solar-svg')

    function makeEl(name, attrs) {
        const el = document.createElementNS(svgNS, name)
        for (const k in attrs) el.setAttribute(k, attrs[k])
        return el
    }

    // Background
    svg.appendChild(makeEl('rect', {
        x: PAD_L, y: PAD_T, width: innerW, height: innerH,
        fill: '#1a1a1a', stroke: '#444', 'stroke-width': 1,
    }))

    // Y-axis ticks
    for (let v = 0; v <= 90; v += 30) {
        const y = yToPx(v)
        svg.appendChild(makeEl('line', {
            x1: PAD_L - 3, y1: y, x2: PAD_L, y2: y,
            stroke: '#888', 'stroke-width': 1,
        }))
        const t = makeEl('text', {
            x: PAD_L - 5, y: y + 3,
            'text-anchor': 'end',
            class: 'hyplan-solar-axis',
        })
        t.textContent = `${v}°`
        svg.appendChild(t)
    }
    // X-axis ticks every 90°
    for (let h = 0; h <= 360; h += 90) {
        const x = xToPx(h)
        svg.appendChild(makeEl('line', {
            x1: x, y1: PAD_T + innerH, x2: x, y2: PAD_T + innerH + 3,
            stroke: '#888', 'stroke-width': 1,
        }))
        const t = makeEl('text', {
            x: x, y: PAD_T + innerH + 14,
            'text-anchor': 'middle',
            class: 'hyplan-solar-axis',
        })
        t.textContent = `${h}`
        svg.appendChild(t)
    }
    // X-axis label
    const xLabel = makeEl('text', {
        x: PAD_L + innerW / 2, y: H - 4,
        'text-anchor': 'middle',
        class: 'hyplan-solar-axis hyplan-solar-axis-label',
    })
    xLabel.textContent = 'Heading (deg)'
    svg.appendChild(xLabel)

    // Threshold line at 25°
    const yThresh = yToPx(25)
    svg.appendChild(makeEl('line', {
        x1: PAD_L, y1: yThresh, x2: PAD_L + innerW, y2: yThresh,
        stroke: '#ef4444', 'stroke-width': 1, 'stroke-dasharray': '3 3',
    }))
    const threshLabel = makeEl('text', {
        x: PAD_L + innerW - 2, y: yThresh - 3,
        'text-anchor': 'end',
        class: 'hyplan-solar-axis',
        fill: '#ef4444',
    })
    threshLabel.textContent = '25°'
    svg.appendChild(threshLabel)

    // Polylines for mean and min glint, clipped to [0, 90]
    function polyline(values, color, dash) {
        const pts = []
        for (let i = 0; i < data.headings.length; i++) {
            const v = values[i]
            if (v == null || isNaN(v)) continue
            const clipped = Math.max(yMin, Math.min(yMax, v))
            pts.push(`${xToPx(data.headings[i]).toFixed(1)},${yToPx(clipped).toFixed(1)}`)
        }
        const attrs = {
            points: pts.join(' '),
            fill: 'none',
            stroke: color,
            'stroke-width': 1.5,
        }
        if (dash) attrs['stroke-dasharray'] = dash
        svg.appendChild(makeEl('polyline', attrs))
    }
    polyline(data.mean_glint, '#0ea5e9')        // mean = solid cyan
    polyline(data.min_glint, '#facc15', '3 2')   // min = dashed yellow

    // Vertical marker at the optimal heading
    const xOpt = xToPx(data.optimal_azimuth)
    svg.appendChild(makeEl('line', {
        x1: xOpt, y1: PAD_T, x2: xOpt, y2: PAD_T + innerH,
        stroke: '#22c55e', 'stroke-width': 1.5, 'stroke-dasharray': '4 3',
    }))
    const optLabel = makeEl('text', {
        x: xOpt + 2, y: PAD_T + 10,
        'text-anchor': 'start',
        class: 'hyplan-solar-axis',
        fill: '#22c55e',
    })
    optLabel.textContent = `opt ${data.optimal_azimuth.toFixed(0)}°`
    svg.appendChild(optLabel)

    // Solar azimuth marker
    if (typeof data.solar_azimuth === 'number') {
        const xSun = xToPx(((data.solar_azimuth % 360) + 360) % 360)
        svg.appendChild(makeEl('line', {
            x1: xSun, y1: PAD_T, x2: xSun, y2: PAD_T + innerH,
            stroke: '#f59e0b', 'stroke-width': 1, 'stroke-dasharray': '2 4',
        }))
        const sLabel = makeEl('text', {
            x: xSun + 2, y: PAD_T + innerH - 4,
            'text-anchor': 'start',
            class: 'hyplan-solar-axis',
            fill: '#f59e0b',
        })
        sLabel.textContent = `sun-az ${data.solar_azimuth.toFixed(0)}°`
        svg.appendChild(sLabel)
    }

    // Legend (bottom-right)
    const legendX = PAD_L + innerW - 80
    const legendY = PAD_T + 4
    function legendEntry(yOffset, color, dash, label) {
        const ly = legendY + yOffset
        const ln = makeEl('line', {
            x1: legendX, y1: ly, x2: legendX + 14, y2: ly,
            stroke: color, 'stroke-width': 1.5,
        })
        if (dash) ln.setAttribute('stroke-dasharray', dash)
        svg.appendChild(ln)
        const t = makeEl('text', {
            x: legendX + 17, y: ly + 3,
            'text-anchor': 'start',
            class: 'hyplan-solar-axis',
        })
        t.textContent = label
        svg.appendChild(t)
    }
    legendEntry(0, '#0ea5e9', null, 'mean')
    legendEntry(10, '#facc15', '3 2', 'min')

    $box.append(svg)
}

function _hmsToHours(hms) {
    // "HH:MM:SS" or "HH:MM" -> decimal hours UTC
    if (!hms) return null
    const parts = hms.split(':')
    const h = parseInt(parts[0], 10)
    const m = parts.length > 1 ? parseInt(parts[1], 10) : 0
    const s = parts.length > 2 ? parseInt(parts[2], 10) : 0
    return h + m / 60 + s / 3600
}

function renderSolarPlot(data, takeoffTimeUtcStr) {
    const $box = $('#hyplan-solar-plot')
    $box.empty()

    const W = 300, H = 180
    const PAD_L = 36, PAD_R = 14, PAD_T = 12, PAD_B = 24
    const innerW = W - PAD_L - PAD_R
    const innerH = H - PAD_T - PAD_B
    const xMin = 0, xMax = 24            // hours UTC
    const yMin = 0, yMax = 180           // zenith degrees (0 = overhead)
    const xToPx = h => PAD_L + ((h - xMin) / (xMax - xMin)) * innerW
    const yToPx = z => PAD_T + ((z - yMin) / (yMax - yMin)) * innerH  // not inverted — small zenith is at top

    const svgNS = 'http://www.w3.org/2000/svg'
    const svg = document.createElementNS(svgNS, 'svg')
    svg.setAttribute('width', W)
    svg.setAttribute('height', H)
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`)
    svg.setAttribute('class', 'hyplan-solar-svg')

    function makeEl(name, attrs) {
        const el = document.createElementNS(svgNS, name)
        for (const k in attrs) el.setAttribute(k, attrs[k])
        return el
    }

    // Plot area background
    svg.appendChild(makeEl('rect', {
        x: PAD_L, y: PAD_T, width: innerW, height: innerH,
        fill: '#1a1a1a', stroke: '#444', 'stroke-width': 1,
    }))

    // Y-axis ticks at every 30°
    for (let z = 0; z <= 180; z += 30) {
        const y = yToPx(z)
        svg.appendChild(makeEl('line', {
            x1: PAD_L - 3, y1: y, x2: PAD_L, y2: y,
            stroke: '#888', 'stroke-width': 1,
        }))
        const t = makeEl('text', {
            x: PAD_L - 5, y: y + 3,
            'text-anchor': 'end',
            class: 'hyplan-solar-axis',
        })
        t.textContent = `${z}°`
        svg.appendChild(t)
    }

    // X-axis ticks every 6 h
    for (let h = 0; h <= 24; h += 6) {
        const x = xToPx(h)
        svg.appendChild(makeEl('line', {
            x1: x, y1: PAD_T + innerH, x2: x, y2: PAD_T + innerH + 3,
            stroke: '#888', 'stroke-width': 1,
        }))
        const t = makeEl('text', {
            x: x, y: PAD_T + innerH + 14,
            'text-anchor': 'middle',
            class: 'hyplan-solar-axis',
        })
        t.textContent = `${h.toString().padStart(2, '0')}`
        svg.appendChild(t)
    }
    // X-axis label
    const xLabel = makeEl('text', {
        x: PAD_L + innerW / 2, y: H - 4,
        'text-anchor': 'middle',
        class: 'hyplan-solar-axis hyplan-solar-axis-label',
    })
    xLabel.textContent = 'Hour of day (UTC)'
    svg.appendChild(xLabel)

    // Horizon reference at zenith=90°
    const yHorizon = yToPx(90)
    svg.appendChild(makeEl('line', {
        x1: PAD_L, y1: yHorizon, x2: PAD_L + innerW, y2: yHorizon,
        stroke: '#9ca3af', 'stroke-width': 1, 'stroke-dasharray': '3 3',
    }))
    const horizonLabel = makeEl('text', {
        x: PAD_L + innerW - 2, y: yHorizon - 3,
        'text-anchor': 'end',
        class: 'hyplan-solar-axis',
    })
    horizonLabel.textContent = 'horizon'
    svg.appendChild(horizonLabel)

    // Zenith polyline
    const pts = data.zenith_deg.map((z, i) => {
        const h = _hmsToHours(data.time_utc[i])
        return `${xToPx(h).toFixed(1)},${yToPx(z).toFixed(1)}`
    }).join(' ')
    svg.appendChild(makeEl('polyline', {
        points: pts,
        fill: 'none',
        stroke: '#0ea5e9',
        'stroke-width': 1.5,
    }))

    // Sunrise / sunset markers
    function drawMarker(hms, color, label) {
        const h = _hmsToHours(hms)
        if (h == null) return
        const x = xToPx(h)
        svg.appendChild(makeEl('line', {
            x1: x, y1: PAD_T, x2: x, y2: PAD_T + innerH,
            stroke: color, 'stroke-width': 1, 'stroke-dasharray': '2 4',
        }))
        const t = makeEl('text', {
            x: x + 2, y: PAD_T + 10,
            'text-anchor': 'start',
            class: 'hyplan-solar-axis',
            fill: color,
        })
        t.textContent = label
        svg.appendChild(t)
    }
    drawMarker(data.sunrise_utc, '#f59e0b', '↑')
    drawMarker(data.sunset_utc, '#f59e0b', '↓')

    // Takeoff-time marker (if user has set one), plotted in UTC to match the axis.
    if (takeoffTimeUtcStr && takeoffTimeUtcStr.length >= 16) {
        const parts = takeoffTimeUtcStr.slice(11, 16)  // "HH:MM"
        const h = _hmsToHours(parts)
        if (h != null) {
            const x = xToPx(h)
            svg.appendChild(makeEl('line', {
                x1: x, y1: PAD_T, x2: x, y2: PAD_T + innerH,
                stroke: '#ef4444', 'stroke-width': 1.5, 'stroke-dasharray': '4 3',
            }))
            const t = makeEl('text', {
                x: x + 2, y: PAD_T + innerH - 4,
                'text-anchor': 'start',
                class: 'hyplan-solar-axis',
                fill: '#ef4444',
            })
            t.textContent = `T₀ ${parts}`
            svg.appendChild(t)
        }
    }

    $box.append(svg)

    // Meta line below plot
    const sunriseStr = data.sunrise_utc || '—'
    const sunsetStr = data.sunset_utc || '—'
    const minZen = Math.min.apply(null, data.zenith_deg).toFixed(1)
    $('#hyplan-solar-meta').html(
        `(${data.lat.toFixed(4)}, ${data.lon.toFixed(4)}) · ${data.date} UTC<br>` +
        `Sunrise ${sunriseStr} · Sunset ${sunsetStr} · min zenith ${minZen}°`
    )
}

export default HyPlanTool
