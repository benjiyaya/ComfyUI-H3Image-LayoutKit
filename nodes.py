import json
import re

import numpy as np
import torch

import torch.nn.functional as F

from . import layout_core as core


class H3BBoxCanvas:
    """Interactive bounding-box editor that works on the classic (Nodes 1.0)
    LiteGraph frontend via a DOM widget. Boxes are stored in `editor_state`
    as normalized JSON; the node renders a full-resolution preview with the
    colored boxes + number tags baked in, and emits box metadata."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "width": ("INT", {"default": 1376, "min": 64, "max": 8192, "step": 16}),
                "height": ("INT", {"default": 768, "min": 64, "max": 8192, "step": 16}),
            },
            "optional": {
                "background": ("IMAGE",),
                "editor_state": ("STRING", {
                    "default": "[]", "multiline": False,
                    "tooltip": "Managed by the canvas widget below. Hand-edited "
                               "or wired JSON re-initializes the canvas.",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE", "STRING", "INT")
    RETURN_NAMES = ("preview", "boxes", "count")
    FUNCTION = "main"
    CATEGORY = "H3-Layout"
    DESCRIPTION = ("Drag to draw boxes, drag edges/corners to resize, click to "
                   "select, set color/description/fill in the toolbar. 'boxes' "
                   "feeds the Layout Prompt Builder; preview is for your eyes — "
                   "keep it OUT of the conditioning image slots.")

    def main(self, width, height, editor_state="[]", background=None):
        boxes = core.parse_state(editor_state)
        boxes = core.to_pixels(boxes, width, height)
        bg = None
        if background is not None:
            bg_np = (background[0].cpu().numpy() if background.ndim == 4
                     else background.cpu().numpy())
            if bg_np.shape[0] != height or bg_np.shape[1] != width:
                # resize to the canvas grid so coords and preview stay aligned
                t = torch.from_numpy(bg_np).permute(2, 0, 1)[None]
                t = F.interpolate(
                    t, size=(height, width), mode="bilinear", align_corners=False)
                bg_np = t[0].permute(1, 2, 0).numpy()
            bg = bg_np
        img = core.render_preview(boxes, width, height, bg)
        out = torch.from_numpy(np.ascontiguousarray(img))[None, ...]
        meta = []
        for b in boxes:
            x1, y1, x2, y2 = b["_frac"]
            m = {k: b[k] for k in ("label", "hex", "desc", "fill", "bbox_px",
                                   "bbox_norm")}
            m.update({"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1})
            meta.append(m)
        return (out, json.dumps(meta), len(boxes))


class H3LayoutPrompt:
    """Deterministic H3-Image layout prompt: RAW single-photo head, one
    'Region NN: coordinates [x1, y1, x2, y2]' line per box (the bbox IS the
    placement signal for its reference character), identity clauses, shared
    -light tail - plus single-scene enforcement sentences (one environment,
    one frame, one continuous floor, one light) so H3 places every region
    inside ONE photograph instead of rendering split panels.

    Widgets, stable order: boxes, extra_brief, [clip], [max_length],
    [env_picture], [coord_mode].
    env_picture names the <Picture N> slot holding the clean environment
    (the base of the whole frame); coord_mode chooses how the bbox reaches
    the model - relational (proportional scene language, no numbers),
    both, or coords (legacy raw 0-1000 lines).
    Unwired clip = pure template. Wired clip (e.g. Qwen3.5-9B) = LLM
    rephrasing, guarded: any output mentioning boxes/rectangles/collage/
    panels/axes - or leaking raw grid numbers in relational mode - or
    failing to generate falls back to the deterministic template."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "boxes": ("STRING", {"multiline": True, "default": "[]",
                                     "tooltip": "wire from H3 BBox Canvas 'boxes'."}),
                "extra_brief": ("STRING", {
                    "multiline": True, "default": "",
                    "tooltip": "Style/lighting/mood. Who each subject IS and "
                               "DOES goes in the canvas box descriptions — with "
                               "empty descriptions each region auto-binds to "
                               "<Picture 2>, <Picture 3>, ... in left-to-right "
                               "order (image_1 must be the clean environment).",
                }),
            },
            "optional": {
                "clip": ("CLIP", {
                    "tooltip": "OPTIONAL LLM rewriter (Load CLIP -> Qwen3.5-9B). "
                               "Unwired = deterministic template. Guarded output: "
                               "guide-word contamination falls back to template.",
                }),
                "max_length": ("INT", {"default": 1024, "min": 64, "max": 8192,
                                       "advanced": True}),
                "env_picture": ("INT", {"default": 1, "min": 1, "max": 16,
                                        "tooltip": "Which <Picture N> slot holds "
                                                   "the CLEAN environment (the base "
                                                   "scene of the whole frame). "
                                                   "Usually 1; set e.g. 4 if your "
                                                   "clean scene is wired to "
                                                   "image_4 and image_1 carries the "
                                                   "box-overlay preview."}),
                "coord_mode": (["relational", "both", "coords"], {
                    "default": "relational",
                    "tooltip": "How the bbox reaches the model. relational = "
                               "proportional scene language only (no numbers - "
                               "raw 0-1000 numbers get drawn as axis tick labels "
                               "around one panel per region). coords = legacy raw "
                               "coordinate lines. both = relational + coordinates.",
                }),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "main"
    CATEGORY = "H3-Layout"
    DESCRIPTION = ("Deterministic bbox-placement layout prompt with "
                   "single-scene enforcement; wire a CLIP in and the LLM "
                   "rephrases it, with template fallback on contamination. "
                   "Keep the canvas preview OUT of image_1 — use the clean "
                   "room image there.")

    def main(self, boxes, extra_brief="", clip=None, max_length=1024,
             env_picture=1, coord_mode="relational"):
        # old workflows saved a 'mode' COMBO value where fields shifted
        if isinstance(extra_brief, str) and extra_brief.strip().lower() in (
                "template", "qwen"):
            extra_brief = ""
        parsed = core.to_pixels(core.parse_state(boxes), 1, 1)
        if not parsed:
            import logging
            logging.warning("H3LayoutPrompt: no valid boxes parsed from input — "
                            "prompt will contain no placement regions. Check the "
                            "canvas 'boxes' wire / editor state.")
        template = core.build_template_prompt(parsed, "", extra_brief,
                                              env_ref=env_picture,
                                              coord_mode=coord_mode)
        if clip is None:
            return (template,)

        request = core.build_llm_request(parsed, "", extra_brief,
                                         env_ref=env_picture,
                                         coord_mode=coord_mode)
        system = (core.SYSTEM_PROMPT if coord_mode == "relational"
                  else core.SYSTEM_PROMPT_COORDS)
        try:
            tokens = clip.tokenize(request, system_prompt=system,
                                   min_length=1, thinking=False)
            generated_ids = clip.generate(
                tokens, do_sample=False, max_length=max_length,
                temperature=1.0, top_k=0, top_p=1.0, min_p=0.0,
                repetition_penalty=1.0, presence_penalty=0.0, seed=None,
                mtp=False)
            text = clip.decode(generated_ids)
            if isinstance(text, list):
                text = text[0]
            text = text.strip()
            if text.startswith("<think>"):
                _, _, text = text.partition("</think>")
                text = text.strip()
        except Exception as exc:  # loader type can't generate, etc.
            import logging
            logging.warning("H3LayoutPrompt: LLM rewrite failed (%s) — "
                            "using template.", exc)
            return (template,)
        banned = re.search(
            r"\b(rectangles?|boxes?|outlines?|guides?|maps?|markers?|"
            r"diagrams?|frameworks?|collages?|panels?|diptychs?|triptychs?|"
            r"split-screens?|axes|axis|tick marks?|rulers?)\b", text, re.I)
        if not banned and coord_mode == "relational" and re.search(
                r"\b\d{2,4}\b", text):
            banned = re.search(r"\b\d{2,4}\b", text)  # raw grid numbers leaked
        if banned:
            import logging
            logging.warning("H3LayoutPrompt: LLM output mentioned %r — "
                            "template fallback to prevent guide bleed.",
                            banned.group(0))
            return (template,)
        return (text if text else template,)


NODE_CLASS_MAPPINGS = {
    "H3BBoxCanvas": H3BBoxCanvas,
    "H3LayoutPrompt": H3LayoutPrompt,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "H3BBoxCanvas": "H3 BBox Canvas (Nodes 1.0)",
    "H3LayoutPrompt": "H3 Layout Prompt Builder",
}
