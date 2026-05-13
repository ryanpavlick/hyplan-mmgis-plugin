// Unit tests for mmgis-tool/HyPlan/helpers.js.
//
// These cover the pure helpers extracted out of HyPlanTool.js — no
// jQuery, no Leaflet, no MMGIS singletons.  Run with `npm test`.

import { describe, expect, it } from 'vitest'

import {
    formatUtcOffset,
    getErrorMessage,
    glintColor,
    GLINT_RDYLBU_STOPS,
    parseLocalDateTimeToUtcIso,
} from '../../mmgis-tool/HyPlan/helpers.js'

describe('getErrorMessage', () => {
    it('returns string detail directly', () => {
        expect(getErrorMessage({ detail: 'Unknown aircraft: foo' }))
            .toBe('Unknown aircraft: foo')
    })

    it('prefers structured detail.message over JSON-stringifying', () => {
        const data = {
            detail: {
                message: 'Solar zenith out of range',
                code: 'hyplan_value_error',
                operation: 'generate-pattern',
            },
        }
        expect(getErrorMessage(data)).toBe('Solar zenith out of range')
    })

    it('JSON-stringifies object detail without a message field', () => {
        // FastAPI's request-body validation returns a list of dicts under
        // detail — no top-level message — and we fall back to a dump.
        const data = { detail: [{ loc: ['body', 'x'], msg: 'field required' }] }
        const out = getErrorMessage(data)
        expect(out).toContain('field required')
    })

    it('falls back to top-level message', () => {
        expect(getErrorMessage({ message: 'something' })).toBe('something')
    })

    it('handles empty / null inputs', () => {
        expect(getErrorMessage({})).toBe('Unknown error')
        expect(getErrorMessage(null)).toBe('Unknown error')
    })
})

describe('glintColor', () => {
    it('returns gray for null / NaN input', () => {
        expect(glintColor(null)).toBe('#888')
        expect(glintColor(undefined)).toBe('#888')
        expect(glintColor(NaN)).toBe('#888')
    })

    it('returns an rgb() string for in-range angles', () => {
        const c = glintColor(45)
        expect(c).toMatch(/^rgb\(\d+, \d+, \d+\)$/)
    })

    it('clamps angles outside [0, 90] to the endpoints', () => {
        expect(glintColor(-10)).toBe(glintColor(0))
        expect(glintColor(120)).toBe(glintColor(90))
    })

    it('maps the endpoints to the colormap stops', () => {
        // At t=0 (angleDeg=0) we hit the first stop exactly: [165, 0, 38].
        expect(glintColor(0)).toBe('rgb(165, 0, 38)')
        // At t=1 (angleDeg=90) we hit the last stop exactly: [49, 54, 149].
        expect(glintColor(90)).toBe('rgb(49, 54, 149)')
    })

    it('produces monotonic mid-range colors (PowerNorm-shaped)', () => {
        // The colormap is roughly hot-to-cold; the red channel should
        // generally decrease as the angle grows past the mid-band.
        const lowR = parseInt(glintColor(10).match(/\d+/)[0], 10)
        const highR = parseInt(glintColor(80).match(/\d+/)[0], 10)
        expect(highR).toBeLessThan(lowR)
    })
})

describe('GLINT_RDYLBU_STOPS', () => {
    it('spans t in [0, 1] with sorted breakpoints', () => {
        expect(GLINT_RDYLBU_STOPS[0][0]).toBe(0)
        expect(GLINT_RDYLBU_STOPS[GLINT_RDYLBU_STOPS.length - 1][0]).toBe(1)
        for (let i = 1; i < GLINT_RDYLBU_STOPS.length; i++) {
            expect(GLINT_RDYLBU_STOPS[i][0])
                .toBeGreaterThan(GLINT_RDYLBU_STOPS[i - 1][0])
        }
    })
})

describe('formatUtcOffset', () => {
    it('formats offset 0 as +00:00', () => {
        // A real Date in UTC has getTimezoneOffset() === 0.  We can't
        // easily construct one without TZ env trickery, so we mock
        // just the method we need.
        const d = { getTimezoneOffset: () => 0 }
        expect(formatUtcOffset(d)).toBe('+00:00')
    })

    it('formats positive offsets (east of UTC)', () => {
        // getTimezoneOffset returns minutes-west, so JST (UTC+9) is -540.
        const d = { getTimezoneOffset: () => -540 }
        expect(formatUtcOffset(d)).toBe('+09:00')
    })

    it('formats negative offsets (west of UTC)', () => {
        // PDT (UTC-7) is +420.
        const d = { getTimezoneOffset: () => 420 }
        expect(formatUtcOffset(d)).toBe('-07:00')
    })

    it('handles fractional-hour offsets', () => {
        // IST (UTC+5:30) is -330.
        const d = { getTimezoneOffset: () => -330 }
        expect(formatUtcOffset(d)).toBe('+05:30')
    })
})

describe('parseLocalDateTimeToUtcIso', () => {
    it('returns null for empty / whitespace-only input', () => {
        expect(parseLocalDateTimeToUtcIso('')).toBeNull()
        expect(parseLocalDateTimeToUtcIso('   ')).toBeNull()
        expect(parseLocalDateTimeToUtcIso(null)).toBeNull()
        expect(parseLocalDateTimeToUtcIso(undefined)).toBeNull()
    })

    it('returns null for unparseable input', () => {
        expect(parseLocalDateTimeToUtcIso('not a date')).toBeNull()
        expect(parseLocalDateTimeToUtcIso('2026-99-99T25:99')).toBeNull()
    })

    it('round-trips a UTC ISO string', () => {
        // Already in UTC: parse then re-stringify should match.
        const out = parseLocalDateTimeToUtcIso('2026-06-15T19:00:00Z')
        expect(out).toBe('2026-06-15T19:00:00Z')
    })

    it('strips the .000 millisecond suffix', () => {
        // Date.toISOString() always emits .000Z for whole seconds;
        // we trim it for cleaner API payloads.
        const out = parseLocalDateTimeToUtcIso('2026-06-15T19:00:00Z')
        expect(out).not.toContain('.000')
        expect(out.endsWith('Z')).toBe(true)
    })
})
