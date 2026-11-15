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
| **Inputs** | `boxes` (from the canvas), `extra_brief` (scene/style text), optional `clip` (a CLIP model with text-generation support, e.g. Qwen loaded via *Load CLIP*), `max_length` |
| **Outputs** | `prompt` (STRING) |

**Deterministic by default — no LLM required:**

- One placement line per box, coordinates `[x1, y1, x2, y2]` normalized to a
  0–1000 grid.
- Box descriptions become subjects verbatim; `<Picture N>` references pass
  through untouched.
- Empty description → the region auto-binds to `the exact subject shown in
  <Picture i+1>` plus an identity clause (sex, face, hair and clothing stay
  identical to the reference — never a generic "the person").
- Short boxes get position-only wording; full-height boxes get
  head-to-feet scale-to-fill wording.
- The output contains **zero** guide vocabulary — words like box, rectangle,
  outline or color names describing placement regions are never emitted.

**Optional LLM mode:** wire a `clip` with generation support and the template is
rephrased into natural prose by the model. The output is guarded: if it mentions
banned guide words (rectangle, box, outline, guide, map, marker, diagram,
framework) or generation fails for any reason, the node silently falls back to
the deterministic template.

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
order consistent with the `image_N` slot order, and keep `image_1` as the clean
environment — the canvas `preview` never goes into a conditioning slot.

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
