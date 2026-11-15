# ComfyUI-H3-Image-LayoutKit

Dev In progress, still optimizing.  Once it's done, a video will be post walkthrough about this.


Two ComfyUI nodes for **MiniMax-H3-Image** workflows: draw placement boxes on an
interactive canvas, and get a clean, model-ready layout prompt out — with
optional LLM rephrasing that is guarded against guide-word contamination.

Works on the classic (Nodes 1.0) LiteGraph frontend.

## Nodes

### H3 BBox Canvas

An interactive bounding-box editor rendered directly in the node graph.

<img width="799" height="698" alt="Screenshot 2026-11-15 201103" src="https://github.com/user-attachments/assets/fe501714-b02c-4ee6-9ad1-527123933474" />

| | |
|---|---|
| **Inputs** | `width`, `height`, `editor_state` (managed by the widget), optional `background` IMAGE |
| **Outputs** | `preview` (IMAGE), `boxes` (JSON STRING), `count` (INT) |

- Drag to draw boxes; drag edges/corners to resize; set color, description and
  fill in the toolbar.
- Click selects (3px drag threshold — no accidental moves); the selected box
  shows 8 resize handles with corner → edge → interior hit priority.
- Right-click deletes, `Esc` deselects, double-click focuses the description
  field; the color swatch row recolors the selection or presets the next box.
- Mouse wheel passes through to graph zoom; no mid-drag reflow.
- `preview` renders the canvas **without** boxes when used as a background
  check — it is for your eyes only. **Never wire `preview` into an H3
  conditioning image slot.**
- `editor_state` holds the boxes as normalized JSON; wiring or hand-editing it
  re-initializes the canvas. Existing graphs keep working across versions —
  node definitions have not changed.

### H3 Layout Prompt Builder

Turns the canvas `boxes` JSON into a final prompt for MiniMax-H3-Image.

<img width="1272" height="867" alt="Screenshot 2026-11-15 201226" src="https://github.com/user-attachments/assets/e301fb45-8c8f-49c5-bfc3-e5e216990ec6" />


| | |
|---|---|
| **Inputs** | `boxes` (from the canvas), `extra_brief` (scene/style text), optional `clip` (a CLIP model with text-generation support, e.g. Qwen loaded via *Load CLIP*), `max_length`, `env_picture` (which `<Picture N>` is the clean environment, default 1), `coord_mode` (`relational` / `both` / `coords`) |
| **Outputs** | `prompt` (STRING) |

**Deterministic by default — no LLM required.** Every bbox is translated into
placement language the model follows, wrapped in single-scene enforcement:

1. **RAW single-photo head** — declares one clean photograph of ONE continuous
   environment, anchored to its reference slot: "the environment shown in
   `<Picture N>` … remains exactly as in that picture", one camera viewpoint,
   one continuous floor, one lighting setup, never separate panels.
2. **One line per box** — `Region 01, in the left third of the frame: stands
   from the floor to about mid-frame height, occupying a narrow area of the
   frame width, immediately to the right of the subject of <Picture 2>.
   Subject and action: <description>.`
3. **Shared-light tail** — one floor, one light, one perspective; only people,
   environment and light; no axes, no numbers, no frames.

### Placement modes (`coord_mode` widget)

| mode | what reaches the model |
|---|---|
| `relational` (default) | proportional scene language only: frame band, width share in words, vertical extent, spacing to the neighbour. **No raw numbers** — test renders showed H3 drawing 0–1000 grid numbers as visible axis tick labels around one panel per region |
| `both` | relational language + `Coordinates on the 0-1000 grid: [x1, y1, x2, y2]` per region |
| `coords` | legacy raw-coordinate lines: `Region 01: coordinates [x1, y1, x2, y2], …` |

The bbox canvas remains the placement signal in all modes — `relational`
translates it instead of deleting it. A/B the modes against your own graph;
in `relational` mode the LLM guard also falls back to the template if raw
grid numbers leak into the rewrite.

### Environment slot (`env_picture` widget)

Names which `<Picture N>` slot holds the **clean** environment that becomes
the base of the whole frame (default 1). If your graph wires the box-overlay
canvas preview into `image_1` and the clean scene into `image_4`, set this to
4 so the prompt anchors the environment to the right picture.

Other design choices:

- **`extra_brief` is scene-level only.** It is emitted with an explicit
  scoping clause — lighting, mood, background elements — and the prompt
  declares that per-subject sentences win any contradiction. Put what each
  subject *does* (kneeling, holding a mic, looking at the window) in that
  box's description field; an unscoped brief with per-subject verbs
  ("subject 1 and 2 are performing") overrides the per-region poses and
  renders stiff standing figures.
- **Single-scene enforcement.** Head and tail declare one unbroken frame —
  one environment, one continuous floor and wall, one viewpoint, one light —
  so regions place subjects *inside one photograph* instead of provoking
  split-panel / collage renders.
- Box descriptions become subjects verbatim; an empty description auto-binds
  the region to `the exact subject shown in <Picture i+1>` plus an identity
  clause (sex, face, hair and clothing stay identical to the reference — never
  a generic "the person").
- The output contains **zero** guide vocabulary — words like box, rectangle,
  outline or color names describing placement regions are never emitted.

**Optional LLM mode:** wire a `clip` with generation support and the template is
rephrased into natural prose by the model, under a system prompt that enforces
the same grammar. The output is guarded: if it mentions banned guide words
(rectangle, box, outline, guide, map, marker, diagram, framework, collage,
panel, diptych, split-screen) or generation fails for any reason, the node
silently falls back to the deterministic template.

## Example wiring (multi-reference composition)

```
Load Image (environment) ─────────────► H3 image_1   (must be the CLEAN scene)
Load Image (person A)    ─────────────► H3 image_2
Load Image (person B)    ─────────────► H3 image_3

H3 BBox Canvas.boxes ──► H3 Layout Prompt Builder.boxes
                          prompt ─────► H3 prompt / Set_Prompt
```

Boxes are resolved **left-to-right**, so with empty descriptions the first box
binds to `<Picture 2>`, the second to `<Picture 3>`, and so on. Keep the box
order consistent with the `image_N` slot order. If your clean environment sits
in a slot other than `image_1` (for example `image_4`, because `image_1`
carries the canvas preview), set `env_picture` to that slot so the prompt
anchors the base scene to the right `<Picture N>`.

## Installation

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/benjiyaya/ComfyUI-H3Image-LayoutKit
```

Or download the ZIP and extract it into `ComfyUI/custom_nodes/`.

Restart ComfyUI, then hard-refresh the browser (Ctrl+F5) so the canvas widget's
JavaScript is reloaded.

## Requirements

No extra pip dependencies — uses `numpy`, `torch` and `Pillow`, which ship with
ComfyUI. The prompt core (`layout_core.py`) is pure Python with no torch/comfy
imports, so it can be unit-tested standalone.

## License

MIT — see [LICENSE](LICENSE).
