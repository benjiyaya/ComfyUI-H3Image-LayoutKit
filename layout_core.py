"""Pure-python core logic for H3-LayoutKit: box state parsing, color naming,
preview rendering, and prompt assembly. No torch / no comfy imports so it can
be unit-tested standalone.
"""

import json

def parse_state(raw):
    """Accept list / JSON str / None -> list of normalized box dicts."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return []
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []  # tolerate hand-corrupted editor state; canvas renders empty
    if isinstance(raw, dict):  # single box
        raw = [raw]
    out = []
    for i, b in enumerate(raw):
        if not isinstance(b, dict):
            continue
        try:
            x, y, w, h = (float(b[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            continue
        x1, x2 = sorted((x, x + w))
        y1, y2 = sorted((y, y + h))
        x1, y1 = max(0.0, min(1.0, x1)), max(0.0, min(1.0, y1))
        x2, y2 = max(0.0, min(1.0, x2)), max(0.0, min(1.0, y2))
        if x2 - x1 < 1e-4 or y2 - y1 < 1e-4:
            continue
        out.append({
            "label": int(b.get("label", i + 1)),
            "hex": str(b.get("color") or b.get("hex") or "#e53935"),
            "desc": str(b.get("desc", "") or ""),
            "fill": bool(b.get("fill", False)),
            "bbox_px": None,
            "bbox_norm": [round(x1 * 1000), round(y1 * 1000),
                          round(x2 * 1000), round(y2 * 1000)],
            "_frac": (x1, y1, x2, y2),
        })
    return out


def to_pixels(boxes, width, height):
    for b in boxes:
        x1, y1, x2, y2 = b["_frac"]
        b["bbox_px"] = [round(x1 * width), round(y1 * height),
                        round(x2 * width), round(y2 * height)]
    return boxes


def hex_to_rgb(value):
    value = value.lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (229, 57, 53)


def render_preview(boxes, width, height, background_rgb=None):
    """background_rgb: optional (H, W, 3) float 0-1 numpy array. Returns same."""
    import numpy as np
    if background_rgb is not None:
        img = (np.clip(background_rgb, 0, 1) * 255).astype("uint8").copy()
        if img.shape[0] != height or img.shape[1] != width:
            img = None
    else:
        img = None
    if img is None:
        img = np.full((height, width, 3), 24, dtype="uint8")

    try:
        from PIL import Image, ImageDraw
        pil = Image.fromarray(img)
        draw = ImageDraw.Draw(pil)
        lw = max(3, int(round(max(width, height) / 260)))
        for b in boxes:
            x1, y1, x2, y2 = b["bbox_px"]
            rgb = hex_to_rgb(b["hex"])
            if b["fill"]:
                filled = Image.new("RGBA", pil.size, (0, 0, 0, 0))
                ImageDraw.Draw(filled).rectangle(
                    [x1, y1, x2, y2], fill=rgb + (46,))
                pil = Image.alpha_composite(pil.convert("RGBA"), filled).convert("RGB")
                draw = ImageDraw.Draw(pil)
            draw.rectangle([x1, y1, x2, y2], outline=rgb, width=lw)
            tag = "{:02d}".format(b["label"])
            fs = max(12, int(round(lw * 5.2)))
            try:
                font = ImageFont_for(fs)
            except Exception:
                font = None
            tw = fs * (1 + len(tag)) + 8
            th = fs + 10
            ty = y1 - th if y1 - th >= 0 else y1
            draw.rectangle([x1, ty, x1 + tw, ty + th], fill=rgb)
            draw.text((x1 + 5, ty + 4), tag, fill=(15, 15, 15), font=font)
        img = np.asarray(pil)
    except ImportError:  # PIL missing: minimal stamped fallback
        for b in boxes:
            x1, y1, x2, y2 = b["bbox_px"]
            rgb = hex_to_rgb(b["hex"])
            lw = max(3, int(round(max(width, height) / 260)))
            img[y1:y1 + lw, x1:x2] = rgb
            img[y2 - lw:y2, x1:x2] = rgb
            img[y1:y2, x1:x1 + lw] = rgb
            img[y1:y2, x2 - lw:x2] = rgb
    return img.astype("float32") / 255.0


def ImageFont_for(size):
    from PIL import ImageFont
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


# ---------------------------------------------------------------- prompts ---
# Prompt grammar follows MiniMax's official H3-Image prompt-writing guide:
# source definitions -> "The target image" overview -> per-subject
# relationships -> shared-light closing, as one continuous prose paragraph.
# Positions are expressed in relational scene language only; raw coordinates
# are never emitted (H3 reads per-region framing clauses as separate panels).

SYSTEM_PROMPT = (
    "You are a prompt writer for MiniMax-H3-Image, a 6-step distilled "
    "image-editing model conditioned on reference pictures labeled "
    "<Picture 1>, <Picture 2>, ... and a text prompt. The user gives you a "
    "layout job: subject definitions with relational placement hints. Write "
    "ONE continuous plain-text prompt in this exact grammar:\n"
    "1. Source definitions first, one sentence per reference in picture "
    "order: 'The ... in <Picture N> is <Subject N>.'\n"
    "2. A target overview beginning with 'The target image': declare ONE "
    "single continuous photograph of the environment of <Subject 1> in "
    "which every subject appears together in the same unbroken frame, "
    "sharing one camera viewpoint, one continuous floor and wall behind "
    "every figure, and one lighting setup.\n"
    "3. Relationships: state that <Subject 1> is partially preserved - its "
    "walls, floor, window, furniture and light remain exactly as shown "
    "while the people are present in the room. Then, for each person, one "
    "placement sentence in relational scene language (left third / center / "
    "right third of the frame, to the left or right of another <Subject>, "
    "full body from the floor to near the ceiling line), weaving the user's "
    "subject description verbatim, followed by '<Subject N> is partially "
    "preserved; their face, hair, skin tone and outfit remain exactly as in "
    "<Picture N> while pose, position and scale adapt to the room.'\n"
    "4. A closing sentence: all figures share the room's single light and "
    "one consistent perspective, with contact shadows on the same "
    "continuous floor; the frame contains only the people, the room and "
    "the light.\n"
    "Hard rules:\n"
    "- Never describe the result as separate photos, a collage, a grid, "
    "panels, a split screen or a diptych, and never give each subject its "
    "own frame, window or background. One room, one exposure.\n"
    "- Express every position in relational scene language only. Never "
    "print coordinates, grids, boxes, rectangles, outlines, guides, maps, "
    "markers, diagrams or frameworks, and never name placement colors.\n"
    "- Keep <Picture N> and <Subject N> tokens exactly as given.\n"
    "- Preserve each subject's exact sex, face, hair, skin tone and outfit "
    "from its reference; never substitute generic words like 'person'.\n"
    "- Never write negative instructions about guides or overlays; state "
    "positively what exists in the frame.\n"
    "- Output only the final prompt text, no preamble, no markdown."
)


def _band_phrase(x1, x2):
    """Horizontal placement in relational scene language (coords 0-1000)."""
    cx = (x1 + x2) / 2000.0
    if cx < 0.18:
        return "at the far left edge of the frame"
    if cx < 0.38:
        return "in the left third of the frame"
    if cx < 0.62:
        return "in the center of the frame"
    if cx < 0.82:
        return "in the right third of the frame"
    return "at the far right edge of the frame"


def _scale_phrase(y1, y2):
    """Vertical extent as a body-scale cue (coords 0-1000)."""
    h = y2 - y1
    if h >= 450:
        return "stands full body from the floor to near the ceiling line"
    if h >= 220:
        return "stands from the floor to about mid-wall height"
    return "is placed"


def _resolved_regions(boxes, first_ref=2):
    """Left-to-right order; box i becomes <Picture first_ref+i> /
    <Subject first_ref+i> with a relational placement hint computed from
    its normalized bbox. Desc (if typed) is woven by the caller."""
    ordered = sorted(boxes, key=lambda x: x["bbox_px"][0] if x["bbox_px"] else 0)
    out = []
    for i, b in enumerate(ordered):
        ref = first_ref + i
        x1, y1, x2, y2 = b["bbox_norm"]
        hint = "{} {}".format(_scale_phrase(y1, y2), _band_phrase(x1, x2))
        if i:
            hint += ", to the right of <Subject {}>".format(ref - 1)
        out.append((b, ref, hint))
    return out


def build_template_prompt(boxes, roles="", extra="", first_ref=2):
    """Deterministic prompt in the official H3 subject grammar: one
    continuous paragraph, single-scene enforced, no coordinates."""
    regions = _resolved_regions(boxes, first_ref)
    parts = ["The interior room in <Picture 1> is <Subject 1>."]
    for _b, ref, _hint in regions:
        parts.append("The person in <Picture {0}> is <Subject {0}>.".format(ref))
    parts.append(
        "The target image is one single continuous photograph of that room: "
        "every subject above appears together in the same unbroken frame, "
        "sharing one camera viewpoint, one continuous floor and wall behind "
        "all figures, and one lighting setup.")
    if roles.strip():
        parts.append(roles.strip())
    parts.append(
        "<Subject 1> is partially preserved; its walls, floor, window, "
        "furniture and light remain exactly as shown while the following "
        "people are present in the room.")
    for b, ref, hint in regions:
        desc = b["desc"].strip()
        who = "<Subject {}>, {},".format(ref, desc) if desc else \
            "<Subject {}>".format(ref)
        parts.append("{} {}.".format(who, hint))
        parts.append(
            "<Subject {0}> is partially preserved; their face, hair, skin "
            "tone and outfit remain exactly as in <Picture {0}> while pose, "
            "position and scale adapt to the room.".format(ref))
    if extra.strip():
        brief = extra.strip()
        if not brief.endswith((".", "!", "?")):
            brief += "."
        parts.append("Additional brief: " + brief)
    parts.append(
        "All figures share the room's single light and one consistent "
        "perspective, with contact shadows falling on the same continuous "
        "floor. The frame is an ordinary photograph: only the people, the "
        "room and the light.")
    return " ".join(parts)


def build_llm_request(boxes, roles="", extra="", first_ref=2):
    """User-message payload for the connected Qwen (or for core Generate Text)."""
    lines = ["Layout job for MiniMax-H3-Image.", "",
             "References, in picture order:",
             "- <Picture 1>: the environment room (<Subject 1>)."]
    for b, ref, hint in _resolved_regions(boxes, first_ref):
        desc = b["desc"].strip()
        line = ("- <Picture {0}>: a person (<Subject {0}>). "
                "Placement hint: {1}.".format(ref, hint))
        if desc:
            line += " Description to weave verbatim: " + desc
        lines.append(line)
    if roles.strip():
        lines += ["", "Reference picture roles:", roles.strip()]
    if extra.strip():
        lines += ["", "User brief:", extra.strip()]
    lines += ["",
              "Write ONE continuous prompt paragraph in the system grammar: "
              "definitions, a 'The target image' overview, one placement "
              "sentence plus one partial-preservation sentence per subject, "
              "then the shared-light closing. Relational scene language "
              "only - no coordinates, no guide words."]
    return "\n".join(lines)
