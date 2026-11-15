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
    """Deterministic H3-Image layout prompt in the official subject grammar
    (definitions -> 'The target image' overview -> per-subject placement and
    partial-preservation sentences -> shared-light closing). Positions are
    relational scene language; no coordinates, so H3 never reads regions as
    separate panels.

    Widgets, stable order: boxes, extra_brief, [clip], [max_length].
    Unwired clip = pure template. Wired clip (e.g. Qwen3.5-9B) = LLM
    rephrasing, guarded: any output mentioning boxes/rectangles/collage/
    panels or failing to generate falls back to the deterministic template."""

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
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "main"
    CATEGORY = "H3-Layout"
    DESCRIPTION = ("Deterministic single-scene layout prompt in H3's subject "
                   "grammar; wire a CLIP in and the LLM rephrases it, with "
                   "template fallback on contamination. Keep the canvas "
                   "preview OUT of image_1 — use the clean room image there.")

    def main(self, boxes, extra_brief="", clip=None, max_length=1024):
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
        template = core.build_template_prompt(parsed, "", extra_brief)
        if clip is None:
            return (template,)

        request = core.build_llm_request(parsed, "", extra_brief)
        try:
            tokens = clip.tokenize(request, system_prompt=core.SYSTEM_PROMPT,
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
            r"split-screens?)\b", text, re.I)
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
