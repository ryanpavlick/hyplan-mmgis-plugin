import $ from 'jquery'
import F_ from '../../Basics/Formulae_/Formulae_'
import L_ from '../../Basics/Layers_/Layers_'
import Map_ from '../../Basics/Map_/Map_'
import './HyPlanTool.css'

// Default service URL (can be overridden in config)
let SERVICE_URL = 'http://localhost:8100'

// State
let campaignId = null
let drawnPolygon = null
let flightLineLayer = null
let planLayer = null
let selectedLineIds = []
let windLayer = null
let patternsLayer = null            // map layer for all patterns (waypoint + line-pattern decoration)
let patternsCache = []              // [{pattern_id, kind, name, is_line_based, ...}]
let patternRefsForCompute = []      // pattern_ids of waypoint patterns to include in compute
let swathLayer = null
let glintLayer = null
let drawLineMode = false
let drawLineStart = null
let patternCenterMode = false
let patternCenter = null
let solarPickMode = false
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

const markup = `
<div id="hyplanTool">
    <h3>HyPlan Flight Planner</h3>

    <div class="hyplan-section">
        <h3>1. Campaign</h3>
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
        <label>Takeoff Time (UTC — not local!)</label>
        <input type="datetime-local" id="hyplan-takeoff-time" value="" />
        <div style="font-size:10px; color:var(--color-c); margin-top:-2px">The browser shows your local clock, but the entered value is sent as UTC. For noon local, enter noon + (your UTC offset).</div>
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
    </div>

    <div class="hyplan-section">
        <h3>2. Generate Flight Lines</h3>
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
    </div>

    <div class="hyplan-section">
        <h3>2b. Individual Lines</h3>
        <p style="font-size:11px; color:var(--color-c)">Click two points on the map to add a line, or enter coordinates.</p>
        <button id="hyplan-add-line-btn">Draw Line on Map</button>
        <button id="hyplan-cancel-draw-btn" style="display:none">Cancel</button>
        <button id="hyplan-delete-line-btn" disabled>Delete Selected Line</button>
        <div id="hyplan-add-line-status" class="hyplan-status"></div>
    </div>

    <div class="hyplan-section">
        <h3>2c. Flight Patterns</h3>
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
    </div>

    <div class="hyplan-section">
        <h3>2d. Patterns in Campaign</h3>
        <p style="font-size:11px; color:var(--color-c)">Delete a pattern as a whole. Line patterns also show each leg in the line list above for individual selection/transform.</p>
        <div id="hyplan-patterns-list" class="hyplan-patterns-list"></div>
    </div>

    <div class="hyplan-section">
        <h3>3. Select & Order Lines</h3>
        <button id="hyplan-select-all-btn">Select All</button>
        <button id="hyplan-clear-selection-btn">Clear</button>
        <button id="hyplan-optimize-btn" disabled>Optimize Order</button>
        <div id="hyplan-line-list" class="hyplan-line-list"></div>
        <div id="hyplan-selection-status" class="hyplan-status"></div>
        <div id="hyplan-optimize-status" class="hyplan-status"></div>
    </div>

    <div class="hyplan-section">
        <h3>3b. Transform Selected Lines</h3>
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
    </div>

    <div class="hyplan-section">
        <h3>4. Compute Flight Plan</h3>
        <button id="hyplan-compute-btn" disabled>Compute Plan</button>
        <button id="hyplan-clear-plan-btn" style="display:none">Clear Plan</button>
        <div id="hyplan-compute-status" class="hyplan-status"></div>
        <div id="hyplan-summary"></div>
    </div>

    <div class="hyplan-section">
        <h3>4b. Swath Display</h3>
        <button id="hyplan-show-swaths-btn" disabled>Generate Swaths</button>
        <button id="hyplan-hide-swaths-btn" style="display:none">Hide Swaths</button>
        <div id="hyplan-swath-status" class="hyplan-status"></div>
    </div>

    <div class="hyplan-section">
        <h3>4c. Glint Analysis</h3>
        <p style="font-size:11px; color:var(--color-c)">For each selected line at the takeoff time and current sensor, plots per-swath-sample glint angle. Rotate or shift selected lines (Section 3b) or change the takeoff time (Section 1) and re-compute to minimize glint.</p>
        <label>Glint threshold (deg)</label>
        <input type="number" id="hyplan-glint-threshold" value="25" step="1" min="1" max="90" />
        <button id="hyplan-show-glint-btn" disabled>Compute Glint</button>
        <button id="hyplan-hide-glint-btn" style="display:none">Hide Glint</button>
        <div id="hyplan-glint-status" class="hyplan-status"></div>
        <div id="hyplan-glint-summary"></div>
    </div>

    <div class="hyplan-section">
        <h3>5. Solar Position</h3>
        <p style="font-size:11px; color:var(--color-c)">Plot solar zenith angle through the day at a chosen point. Pick a location on the map, or use the midpoint of a selected flight line.</p>
        <button id="hyplan-pick-solar-point-btn">Plot at Map Point</button>
        <button id="hyplan-cancel-pick-solar-btn" style="display:none">Cancel</button>
        <button id="hyplan-solar-from-line-btn" disabled>Plot at Selected Line</button>
        <button id="hyplan-hide-solar-btn" style="display:none">Hide Plot</button>
        <div id="hyplan-solar-status" class="hyplan-status"></div>
        <div id="hyplan-solar-plot"></div>
        <div id="hyplan-solar-meta" style="font-size:11px; color:var(--color-c); margin-top:4px"></div>
    </div>

    <div class="hyplan-section">
        <h3>6. Export</h3>
        <button id="hyplan-export-btn" disabled>Export KML + GPX</button>
        <div id="hyplan-export-status" class="hyplan-status"></div>
        <div id="hyplan-download-links"></div>
    </div>
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

    // Show Wind on Map
    $('#hyplan-show-wind-btn').on('click', function () {
        const windKind = $('#hyplan-wind-kind').val()
        if (windKind !== 'gfs' && windKind !== 'gmao') {
            $('#hyplan-wind-status').text('Select GFS or GMAO wind source to visualize.')
            return
        }

        const bounds = Map_.map.getBounds()
        const altitude = parseFloat($('#hyplan-altitude').val()) || 3000
        const takeoffTime = $('#hyplan-takeoff-time').val()
        const time = takeoffTime ? takeoffTime + ':00Z' : new Date().toISOString()

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
            $('#hyplan-optimize-azimuth-status').text('Error: Set a takeoff time (UTC) first — Section 1.')
            return
        }
        const takeoffTime = takeoffTimeVal + ':00Z'
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
        const takeoffTimeVal = $('#hyplan-takeoff-time').val()
        const takeoffTime = takeoffTimeVal ? takeoffTimeVal + ':00Z' : null

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

        fetch(`${SERVICE_URL}/generate-swaths`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                campaign_id: campaignId,
                line_ids: selectedLineIds,
                sensor: sensor,
                altitude_msl_m: altitude,
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
        const takeoffTime = takeoffTimeVal + ':00Z'
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
        const name = $('#hyplan-campaign-name').val() || 'Mission'

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
        patternCenterMode = true
        patternCenter = null
        $('#hyplan-set-pattern-center-btn').hide()
        $('#hyplan-cancel-pattern-btn').show()
        $('#hyplan-pattern-status').text('Click the map to set the pattern center.')
        Map_.map.on('click', onPatternCenterClick)
    })

    $('#hyplan-cancel-pattern-btn').on('click', function () {
        patternCenterMode = false
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

    function onPatternCenterClick(e) {
        patternCenter = e.latlng
        patternCenterMode = false
        Map_.map.off('click', onPatternCenterClick)
        $('#hyplan-cancel-pattern-btn').hide()
        $('#hyplan-set-pattern-center-btn').show()
        $('#hyplan-generate-pattern-btn').prop('disabled', false)
        $('#hyplan-pattern-status').text(`Center: (${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)})`)
    }

    // --- Section 5: Solar Position --------------------------------------
    $('#hyplan-pick-solar-point-btn').on('click', function () {
        solarPickMode = true
        $('#hyplan-pick-solar-point-btn').hide()
        $('#hyplan-cancel-pick-solar-btn').show()
        $('#hyplan-solar-status').text('Click the map to plot solar zenith here.')
        Map_.map.on('click', onSolarPickClick)
    })

    $('#hyplan-cancel-pick-solar-btn').on('click', function () {
        solarPickMode = false
        Map_.map.off('click', onSolarPickClick)
        $('#hyplan-cancel-pick-solar-btn').hide()
        $('#hyplan-pick-solar-point-btn').show()
        $('#hyplan-solar-status').text('')
    })

    function onSolarPickClick(e) {
        solarPickMode = false
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
        const takeoffTimeVal = $('#hyplan-takeoff-time').val()
        const takeoffTime = takeoffTimeVal ? takeoffTimeVal + ':00Z' : null
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

            const label = data.is_line_based ? 'flight lines' : 'waypoints'
            $('#hyplan-pattern-status').text(
                `Generated ${data.pattern_name} (${data.pattern_kind}). Added to campaign as ${label}.`
            )
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
        hyplanDisownAndRemove(solarMarkerLayer); solarMarkerLayer = null
        campaignId = null
        selectedLineIds = []
    }
}

// --- Helper functions ---

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
        style: function (feature) {
            return {
                color: '#3b82f6',
                weight: 4,
                opacity: 0.8,
            }
        },
        onEachFeature: function (feature, layer) {
            const name = feature.properties.site_name || feature.properties.line_id
            layer.bindTooltip(name, { sticky: true })
            layer.on('click', function () {
                const lineId = feature.properties.line_id || feature.id
                toggleLineSelection(lineId)
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

function getErrorMessage(data) {
    if (data.detail) return typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    if (data.message) return data.message
    return 'Unknown error'
}

// Approximation of Matplotlib RdYlBu colormap with PowerNorm(gamma=0.4, vmin=0, vmax=90)
// to match notebooks/glint_analysis.ipynb Cell 10 styling. Red = low glint angle (bad,
// near-specular), blue = high (good).
const _GLINT_RDYLBU_STOPS = [
    [0.00, [165,   0,  38]],
    [0.10, [215,  48,  39]],
    [0.20, [244, 109,  67]],
    [0.30, [253, 174,  97]],
    [0.40, [254, 224, 144]],
    [0.50, [255, 255, 191]],
    [0.60, [224, 243, 248]],
    [0.70, [171, 217, 233]],
    [0.80, [116, 173, 209]],
    [0.90, [ 69, 117, 180]],
    [1.00, [ 49,  54, 149]],
]

function glintColor(angleDeg) {
    if (angleDeg == null || isNaN(angleDeg)) return '#888'
    const x = Math.max(0, Math.min(angleDeg, 90)) / 90
    const t = Math.pow(x, 0.4)  // PowerNorm(gamma=0.4)
    let lo = _GLINT_RDYLBU_STOPS[0]
    let hi = _GLINT_RDYLBU_STOPS[_GLINT_RDYLBU_STOPS.length - 1]
    for (let i = 1; i < _GLINT_RDYLBU_STOPS.length; i++) {
        if (t <= _GLINT_RDYLBU_STOPS[i][0]) {
            lo = _GLINT_RDYLBU_STOPS[i - 1]
            hi = _GLINT_RDYLBU_STOPS[i]
            break
        }
    }
    const span = hi[0] - lo[0]
    const frac = span > 0 ? (t - lo[0]) / span : 0
    const r = Math.round(lo[1][0] + (hi[1][0] - lo[1][0]) * frac)
    const g = Math.round(lo[1][1] + (hi[1][1] - lo[1][1]) * frac)
    const b = Math.round(lo[1][2] + (hi[1][2] - lo[1][2]) * frac)
    return `rgb(${r}, ${g}, ${b})`
}

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
        },
    })).addTo(Map_.map)
}

function renderPatternsList() {
    const $list = $('#hyplan-patterns-list')
    $list.empty()
    if (patternsCache.length === 0) {
        $list.append('<p class="hyplan-empty">No patterns in this campaign.</p>')
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
}

function refreshPatternsCache() {
    if (!campaignId) return Promise.resolve()
    return fetch(`${SERVICE_URL}/patterns/${campaignId}`)
        .then(r => r.json())
        .then(data => {
            patternsCache = (data.patterns || []).map(p => ({
                pattern_id: p.pattern_id,
                kind: p.kind,
                name: p.name,
                is_line_based: p.is_line_based,
            }))
            // Drop refs to patterns that no longer exist
            const ids = new Set(patternsCache.map(p => p.pattern_id))
            patternRefsForCompute = patternRefsForCompute.filter(id => ids.has(id))
            renderPatternsList()
        })
        .catch(() => { /* non-fatal */ })
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
            $('#hyplan-pattern-status').text(`Deleted pattern ${patternId}.`)
        })
        .catch(err => {
            $('#hyplan-pattern-status').text('Delete error: ' + err.message)
        })
}

// --- Solar Position helpers ---

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
    const takeoffTimeVal = $('#hyplan-takeoff-time').val()
    let date
    if (takeoffTimeVal && takeoffTimeVal.length >= 10) {
        date = takeoffTimeVal.slice(0, 10)
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
        renderSolarPlot(data, takeoffTimeVal)
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

function renderSolarPlot(data, takeoffTimeLocalStr) {
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

    // Takeoff-time marker (if user has set one). The datetime-local input
    // is treated as UTC throughout the plugin, so use it directly.
    if (takeoffTimeLocalStr && takeoffTimeLocalStr.length >= 16) {
        // takeoffTimeLocalStr is "YYYY-MM-DDTHH:MM"
        const parts = takeoffTimeLocalStr.slice(11)  // "HH:MM"
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
