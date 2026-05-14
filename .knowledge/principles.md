# UI Design Principles

These are the design principles for the HyPlan MMGIS plugin.  They
shape what gets built into the panel vs. into the map, what goes
into the service vs. the browser, and how the workflow is exposed.
Treat them as the tie-breaker when an implementation choice could
go either way.

## The ten

1. **Map first.**  The map is the main canvas; the panel supports
   it.
2. **Thin client.**  The browser captures intent, shows state, and
   renders results.  HyPlan logic stays in the service.
3. **Workflow over dashboard.**  Organize the tool around the
   mission-planning sequence, not around data models.
4. **Direct manipulation first.**  Prefer picking, drawing,
   selecting, and contextual map actions before asking for raw
   coordinates.
5. **Parameters stay local to the action.**  Inputs should live
   beside the command they affect, not in a global settings area.
6. **Derived products are ephemeral.**  Swaths, glint, wind, solar,
   and plan previews should feel like overlays you can regenerate
   or clear.
7. **One section, one verb.**  Each UI block should correspond to a
   clear user intent.
8. **Progressive disclosure.**  Show the common path first; tuck
   advanced controls into collapsible details.
9. **State should be inspectable.**  Current campaign, selected
   lines, included patterns, and active overlays should always be
   understandable from the UI.
10. **Spatial context beats form complexity.**  When a choice is
    easier to understand on the map, put it there.

## How to apply them

When a feature lands, walk the list:

- *Where could this happen on the map instead of in the panel?*
  (#1, #4, #10)
- *Does this need the service, or can the browser do it?*  (#2)
  Default to the service for anything geodesic, planning, or
  domain-specific; reserve the browser for UI state, layer
  lifecycle, and trivial math.
- *Where does this fit in the planning sequence?*  Number the
  section accordingly; don't add an unsequenced "Settings" panel
  (#3).
- *Are the inputs next to the button that uses them?*  (#5)
- *If this produces an overlay (swath / glint / solar / plan / wind)
  — is there a clear way to clear it?*  (#6)
- *Does the section have a single, namable intent?*  If you find
  yourself saying "this section does X and also Y," split it.  (#7)
- *Is the common case visible by default and advanced options
  hidden?*  (#8)
- *Can the user, at any time, look at the panel and the map and
  understand what's loaded and what's selected?*  (#9)

## Implications already in flight

- Right-click map context menus (v0.3) honour **#1, #4, #10** — per-
  object operations (reverse, rotate, translate, delete) live on
  the map rather than in a panel button after a list-select.
- The accordion (v0.3) is the cheapest form of **#8** — common
  path stays expanded, advanced sections collapse.
- The structured-error classifier and `If-Match`/`revision_mismatch`
  guard (v0.2/v0.4) honour **#2 and #9** — the server is the
  source of truth for state and validity; the UI just surfaces it.
- "Set Center on Map" for pattern generation honours **#10**.
- Coverage % readout on `/generate-swaths` (v0.2) honours **#9** —
  derived state is visible alongside its overlay.

## Open gaps to fix going forward

- *Relative-to calculator* asks for raw anchor lat/lon — should
  support clicking an anchor on the map.  (**#4, #10**)
- *Altitude* lives in Section 2 but gets reused by Section 2b
  (Individual Lines) and elsewhere — violates **#5**.  Move it to
  each section that needs it, or treat altitude as inferable from
  context (the active campaign).
- *Section 1 (Campaign)* bundles name, aircraft, sensor, both
  airports, takeoff time, and wind — violates **#7**.  These don't
  all belong together; some (e.g. wind source) are properties of a
  given compute, not the campaign as a whole.
- *Solar Position* and *Optimize Azimuth* are dashboard-y
  (show me a plot) rather than workflow steps — fits modal
  invocation better than a permanent section (**#3, #7**).
