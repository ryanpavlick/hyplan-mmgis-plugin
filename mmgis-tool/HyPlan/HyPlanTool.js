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
let patternLayer = null
let drawLineMode = false
let drawLineStart = null
let patternCenterMode = false
let patternCenter = null

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
        <label>Takeoff Time (UTC)</label>
        <input type="datetime-local" id="hyplan-takeoff-time" value="" />
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
        </select>
        <label>Heading (deg)</label>
        <input type="number" id="hyplan-pattern-heading" value="0" />
        <div id="hyplan-pattern-params">
            <label>Leg Length (m)</label>
            <input type="number" id="hyplan-pattern-leg-length" value="10000" />
            <label>Radius (m)</label>
            <input type="number" id="hyplan-pattern-radius" value="5000" />
        </div>
        <button id="hyplan-set-pattern-center-btn">Set Center on Map</button>
        <button id="hyplan-cancel-pattern-btn" style="display:none">Cancel</button>
        <button id="hyplan-generate-pattern-btn" disabled>Generate Pattern</button>
        <div id="hyplan-pattern-status" class="hyplan-status"></div>
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
        <h3>4. Compute Flight Plan</h3>
        <button id="hyplan-compute-btn" disabled>Compute Plan</button>
        <div id="hyplan-compute-status" class="hyplan-status"></div>
        <div id="hyplan-summary"></div>
    </div>

    <div class="hyplan-section">
        <h3>5. Export</h3>
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
            if (windLayer) {
                Map_.map.removeLayer(windLayer)
            }
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
            windLayer.addTo(Map_.map)
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
        if (windLayer) {
            Map_.map.removeLayer(windLayer)
            windLayer = null
        }
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
        if (!campaignId || selectedLineIds.length === 0) {
            $('#hyplan-compute-status').text('Select at least one flight line.')
            return
        }

        const aircraft = $('#hyplan-aircraft').val()
        const sequence = selectedLineIds.map(lid => ({
            kind: 'line',
            line_id: lid,
        }))

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
        })
        .catch(err => {
            $('#hyplan-compute-status').text('Error: ' + err.message)
        })
        .finally(() => {
            $('#hyplan-compute-btn').prop('disabled', false)
        })
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

    // --- Flight Patterns ---
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

    function onPatternCenterClick(e) {
        patternCenter = e.latlng
        patternCenterMode = false
        Map_.map.off('click', onPatternCenterClick)
        $('#hyplan-cancel-pattern-btn').hide()
        $('#hyplan-set-pattern-center-btn').show()
        $('#hyplan-generate-pattern-btn').prop('disabled', false)
        $('#hyplan-pattern-status').text(`Center: (${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)})`)
    }

    $('#hyplan-generate-pattern-btn').on('click', function () {
        if (!patternCenter) return

        const pattern = $('#hyplan-pattern-type').val()
        const heading = parseFloat($('#hyplan-pattern-heading').val()) || 0
        const altitude = parseFloat($('#hyplan-altitude').val()) || 3000
        const legLength = parseFloat($('#hyplan-pattern-leg-length').val()) || 10000
        const radius = parseFloat($('#hyplan-pattern-radius').val()) || 5000
        const name = $('#hyplan-campaign-name').val() || 'Mission'

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
                params: {
                    leg_length_m: legLength,
                    radius_m: radius,
                    n_legs: 2,
                    n_lines: 4,
                    n_sides: 4,
                    n_cycles: 2,
                    n_turns: 3,
                },
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (data.detail || data.message) {
                $('#hyplan-pattern-status').text('Error: ' + getErrorMessage(data))
                return
            }
            campaignId = data.campaign_id
            // Display pattern on map
            if (patternLayer) Map_.map.removeLayer(patternLayer)
            patternLayer = window.L.geoJSON(data.waypoints, {
                style: { color: '#f59e0b', weight: 3, opacity: 0.9, dashArray: '5 5' },
                pointToLayer: function (feature, latlng) {
                    return window.L.circleMarker(latlng, {
                        radius: 5, fillColor: '#f59e0b', color: '#fff',
                        weight: 1, opacity: 1, fillOpacity: 0.8,
                    })
                },
                onEachFeature: function (feature, layer) {
                    if (feature.properties.name) {
                        layer.bindTooltip(feature.properties.name, { sticky: true })
                    }
                },
            }).addTo(Map_.map)
            $('#hyplan-pattern-status').text(`Generated ${data.waypoint_count} waypoints (${pattern}).`)
        })
        .catch(err => {
            $('#hyplan-pattern-status').text('Error: ' + err.message)
        })
        .finally(() => {
            $('#hyplan-generate-pattern-btn').prop('disabled', false)
        })
    })

    function separateFromMMGIS() {
        if (flightLineLayer) {
            Map_.map.removeLayer(flightLineLayer)
            flightLineLayer = null
        }
        if (planLayer) {
            Map_.map.removeLayer(planLayer)
            planLayer = null
        }
        if (windLayer) {
            Map_.map.removeLayer(windLayer)
            windLayer = null
        }
        if (patternLayer) {
            Map_.map.removeLayer(patternLayer)
            patternLayer = null
        }
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
    if (flightLineLayer) {
        Map_.map.removeLayer(flightLineLayer)
    }
    flightLineLayer = window.L.geoJSON(geojson, {
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
    }).addTo(Map_.map)
}

function displayPlan(geojson) {
    if (planLayer) {
        Map_.map.removeLayer(planLayer)
    }
    const segmentColors = {
        takeoff: '#ef4444',
        climb: '#f97316',
        transit: '#6b7280',
        descent: '#3b82f6',
        flight_line: '#22c55e',
        approach: '#8b5cf6',
    }
    planLayer = window.L.geoJSON(geojson, {
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
    }).addTo(Map_.map)
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
    $('#hyplan-compute-btn').prop('disabled', selected === 0)
    $('#hyplan-optimize-btn').prop('disabled', selected < 2)
    $('#hyplan-delete-line-btn').prop('disabled', selected === 0)
}

function getErrorMessage(data) {
    if (data.detail) return typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)
    if (data.message) return data.message
    return 'Unknown error'
}

export default HyPlanTool
