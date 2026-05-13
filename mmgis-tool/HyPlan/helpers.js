// Pure helpers extracted from HyPlanTool.js so they're testable
// outside an MMGIS host (no jQuery, no Leaflet, no MMGIS singletons).
//
// HyPlanTool.js imports these via:
//
//     import { getErrorMessage, glintColor, formatUtcOffset,
//              parseLocalDateTimeToUtcIso } from './helpers.js'
//
// Keep this file dependency-free.  Anything that needs DOM access,
// jQuery, Leaflet, or MMGIS singletons stays in HyPlanTool.js.

// --- Error message extraction --------------------------------------------

// Service errors come back in one of two shapes:
//   { detail: "string" }                            -- FastAPI validation, legacy 400s
//   { detail: { message, code, operation } }        -- classified by service.errors.raise_http
// Prefer the structured `message`; fall back to stringifying `detail`
// for older error shapes (FastAPI's request-body validation returns a
// list of dicts under `detail`).
export function getErrorMessage(data) {
    if (data == null) return 'Unknown error'
    if (data.detail) {
        if (typeof data.detail === 'string') return data.detail
        if (typeof data.detail === 'object' && data.detail.message) return data.detail.message
        return JSON.stringify(data.detail)
    }
    if (data.message) return data.message
    return 'Unknown error'
}

// --- Glint colormap ------------------------------------------------------

// Approximation of Matplotlib RdYlBu colormap with PowerNorm(gamma=0.4,
// vmin=0, vmax=90) to match notebooks/glint_analysis.ipynb Cell 10
// styling.  Red = low glint angle (bad, near-specular), blue = high (good).
export const GLINT_RDYLBU_STOPS = [
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

export function glintColor(angleDeg) {
    if (angleDeg == null || isNaN(angleDeg)) return '#888'
    const x = Math.max(0, Math.min(angleDeg, 90)) / 90
    const t = Math.pow(x, 0.4)  // PowerNorm(gamma=0.4)
    let lo = GLINT_RDYLBU_STOPS[0]
    let hi = GLINT_RDYLBU_STOPS[GLINT_RDYLBU_STOPS.length - 1]
    for (let i = 1; i < GLINT_RDYLBU_STOPS.length; i++) {
        if (t <= GLINT_RDYLBU_STOPS[i][0]) {
            lo = GLINT_RDYLBU_STOPS[i - 1]
            hi = GLINT_RDYLBU_STOPS[i]
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

// --- Time formatting -----------------------------------------------------

// Format the local UTC offset of a Date as "+HH:MM" / "-HH:MM".
export function formatUtcOffset(date) {
    const offsetMin = -date.getTimezoneOffset()
    const sign = offsetMin >= 0 ? '+' : '-'
    const absMin = Math.abs(offsetMin)
    const hours = String(Math.floor(absMin / 60)).padStart(2, '0')
    const minutes = String(absMin % 60).padStart(2, '0')
    return `${sign}${hours}:${minutes}`
}

// Parse a local datetime string (as returned by an HTML
// <input type="datetime-local">) into a UTC ISO string suitable for
// the service API.  Returns null for empty / invalid input.
export function parseLocalDateTimeToUtcIso(raw) {
    if (raw == null) return null
    const trimmed = String(raw).trim()
    if (!trimmed) return null
    const date = new Date(trimmed)
    if (Number.isNaN(date.getTime())) return null
    return date.toISOString().replace('.000Z', 'Z')
}
