# Map and Globe Knob Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make both RadioGarden and Constellation geographic screens fully operable with the three-knob interface introduced by PR #8.

**Architecture:** Extend the existing semantic controller and pure UI adapter instead of binding hardware input directly to display state. The display applies semantic map commands through extracted helpers shared with the current touch actions, so receiver connection and constellation setup retain one implementation.

**Tech Stack:** Python 3, `unittest`, pygame/OpenGL UI state, existing `KnobController` and adapter modules

## Global Constraints

- Remain stacked on `pr/knob-ui`; do not open the upstream PR before PR #8 merges.
- Preserve touch behavior and RC-28 behavior.
- Keep FM-DX and all CAD/product-design files out of this branch.
- Reuse current map bounds and receiver connection paths.

---

### Task 1: Map semantics and pure adapter helpers

**Files:**
- Modify: `UI/knob_controller.py`
- Modify: `UI/knob_ui_adapter.py`
- Test: `UI/test_knob_controller.py`
- Test: `UI/test_knob_ui_adapter.py`

**Interfaces:**
- Consumes: `KnobContext.map_active`, `KnobCommandKind.MAP_PAN_X`, `MAP_PAN_Y`, and `MAP_ZOOM`.
- Produces: `MAP_TARGET_CONTROL_ID`, `RADIOGARDEN_CONTROL_IDS`, `constellation_control_id(server)`, `constellation_server(control_id)`, `apply_map_pan(...)`, `apply_map_zoom(...)`, and map-aware `knob_context(...)`.

- [ ] **Step 1: Write failing controller tests**

Add tests asserting TUNE emits `MAP_PAN_X`, VIEW emits `MAP_PAN_Y`, VIEW press changes the next VIEW turn to `MAP_ZOOM`, and NAV activates the focused map target.

- [ ] **Step 2: Run the focused controller tests and confirm the new assertions fail where behavior is incomplete**

Run: `python3 -m unittest UI.test_knob_controller -v`

- [ ] **Step 3: Write failing adapter tests**

Assert RadioGarden controls equal `("map_target", "map_list", "map_view", "back")`, Constellation controls include stable encoded server IDs, longitude wraps, latitude clamps, and zoom uses a positive multiplicative factor while respecting minimum and maximum bounds.

- [ ] **Step 4: Implement the adapter contracts**

Use URL quoting for stable server IDs, set `map_active=True` for both geographic contexts, and keep math helpers free of pygame/display dependencies.

- [ ] **Step 5: Run the focused pure test suite**

Run: `python3 -m unittest UI.test_knob_controller UI.test_knob_ui_adapter -v`
Expected: all tests pass.

- [ ] **Step 6: Commit the semantic layer**

```bash
git add UI/knob_controller.py UI/knob_ui_adapter.py UI/test_knob_controller.py UI/test_knob_ui_adapter.py docs/superpowers/specs/2026-09-21-map-globe-knob-controls-design.md docs/superpowers/plans/2026-09-21-map-globe-knob-controls.md
git commit -m "feat: add map knob interaction semantics"
```

### Task 2: RadioGarden and Constellation runtime integration

**Files:**
- Modify: `UI/kiwi_gl_display.py`
- Modify: `UI/test_knob_ui.py`

**Interfaces:**
- Consumes: the Task 1 context constructors, focus IDs, stable constellation server decoder, `apply_map_pan(...)`, and `apply_map_zoom(...)`.
- Produces: runtime handling for semantic map commands and activation of both geographic screens.

- [ ] **Step 1: Write failing UI integration tests**

Add source-level/import-safe assertions that the display exposes distinct `receiver_map` and `constellation` contexts, resolves their focus boxes, and handles `MAP_PAN_X`, `MAP_PAN_Y`, and `MAP_ZOOM`.

- [ ] **Step 2: Run the UI test and verify failure**

Run: `python3 -m unittest UI.test_knob_ui -v`

- [ ] **Step 3: Add distinct runtime contexts and focus geometry**

Return `receiver_map` when the picker map is visible and `constellation` when the globe is visible. Provide RadioGarden controls and stable listener targets to `knob_context()`. Resolve the reticle, existing list/view/exit controls, listener cards, and existing Back button.

- [ ] **Step 4: Extract shared activation helpers from touch branches**

Create one helper for starting/reseeding a constellation around an anchor and one helper for selecting a warmed listener. Make both touch and knob activation call those helpers without changing their existing state transitions.

- [ ] **Step 5: Apply map pan and zoom commands**

Use adapter math to update the active screen's yaw, pitch, and scale. Cancel RadioGarden inertia/animation targets before applying direct knob movement and preserve the existing screen-specific limits.

- [ ] **Step 6: Run UI and pure knob tests**

Run: `python3 -m unittest UI.test_knob_ui UI.test_knob_controller UI.test_knob_ui_adapter -v`
Expected: all tests pass.

- [ ] **Step 7: Commit runtime integration**

```bash
git add UI/kiwi_gl_display.py UI/test_knob_ui.py
git commit -m "feat: control receiver maps with hardware knobs"
```

### Task 3: Regression verification and fork publication

**Files:**
- Verify: `UI/*.py`

**Interfaces:**
- Consumes: completed Task 1 and Task 2 behavior.
- Produces: verified stacked branch `pr/map-globe-knobs` on the public fork.

- [ ] **Step 1: Run all knob tests**

Run: `python3 -m unittest discover -s UI -p 'test_knob*.py' -v`
Expected: all tests pass.

- [ ] **Step 2: Compile the changed Python files**

Run: `python3 -m py_compile UI/knob_controller.py UI/knob_ui_adapter.py UI/kiwi_gl_display.py`
Expected: no output and exit code 0.

- [ ] **Step 3: Audit the branch diff and exclusions**

Run: `git diff --check pr/knob-ui...HEAD`

Run: `git diff --name-only pr/knob-ui...HEAD`

Confirm that only UI source/tests and the design/plan documents appear, with no CAD or FM-DX files.

- [ ] **Step 4: Push and verify the public fork ref**

```bash
git push -u public-fork pr/map-globe-knobs
git ls-remote --heads public-fork pr/map-globe-knobs
```

Confirm the remote hash matches local `HEAD`. Do not open an upstream PR while PR #8 remains unmerged.
