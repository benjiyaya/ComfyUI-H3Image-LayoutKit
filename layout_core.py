"""Pure-python core logic for H3-LayoutKit: box state parsing, preview
rendering, and prompt assembly. No torch / no comfy imports so it can be
unit-tested standalone.
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
# The bbox canvas IS the placement signal - but raw 0-1000 numbers must not
# reach the model as a chart spec: test renders showed H3 drawing the grid
# numbers as visible axis tick labels around one panel per region. The
# default "relational" mode therefore translates every bbox into
# proportional scene language (frame band, width share in words, vertical
# extent, spacing to the neighbour) and anchors the environment to its
# <Picture N> slot. coord_mode "coords" reproduces the legacy raw-coordinate
# lines and "both" emits relational + coordinates, for A/B testing.

SYSTEM_PROMPT = (
    "You are a prompt writer for MiniMax-H3-Image, a 6-step distilled "
    "image-editing model conditioned on reference pictures labeled "
    "<Picture 1>, <Picture 2>, ... and a text prompt. The user gives you a "
    "layout job: an environment reference, and one placement region per "
    "subject, already translated into proportional scene language. Write "
    "ONE final prompt paragraph set for H3-Image.\n"
    "Rules:\n"
    "1. Describe ONLY the final photograph. The output must never contain "
    "the words rectangle, box, outline, guide, map, marker, diagram, "
    "framework, collage, panel, split-screen, diptych, axis, axes, tick or "
    "ruler, never any coordinate numbers or percentages, and no color name "
    "used to describe a placement region. Placement colors exist only in "
    "the user's notes to you; they are not part of the image.\n"
    "2. Open by declaring the image a RAW, clean photograph of ONE single "
    "continuous environment - exactly the setting shown in the environment "
    "reference picture, kept as the base of the whole frame: one camera "
    "viewpoint, one continuous floor and wall behind every figure, one "
    "lighting setup. Every subject stands together inside that same frame - "
    "never in separate photos, panels or a split screen.\n"
    "3. Place each subject with the proportional scene language given "
    "(frame band, width share in words, vertical extent, spacing to the "
    "neighbouring subject), e.g. 'in the center of the frame, occupying a "
    "narrow area, stands full body from the floor to near the top of the "
    "frame, immediately to the left of the other artist'. Add a physically "
    "consistent contact shadow on the same floor as every other subject.\n"
    "4. Preserve each subject's exact sex, face, hair, skin tone, outfit "
    "and proportions as shown in its reference picture; name those "
    "attributes from the reference, never substitute generic words like "
    "'person'; require one shared lighting setup consistent with the "
    "environment.\n"
    "5. Never write negative instructions like 'must NOT show the box'; "
    "state positively that only people, environment and light exist in the "
    "frame.\n"
    "6. The user brief adds scene-level context only (lighting, mood, "
    "background elements); it must never change a subject's pose, position, "
    "scale or identity given per subject. If the brief seems to contradict "
    "a subject sentence, the subject sentence wins.\n"
    "7. Output only the final prompt text, no preamble, no markdown."
)

SYSTEM_PROMPT_COORDS = (
    "You are a prompt writer for MiniMax-H3-Image, a 6-step distilled "
    "image-editing model conditioned on reference pictures labeled "
    "<Picture 1>, <Picture 2>, ... and a text prompt. The user gives you a "
    "layout job: reference images, and region placements given as "
    "coordinates. Write ONE final prompt paragraph set for H3-Image.\n"
    "Rules:\n"
    "1. Describe ONLY the final photograph. The output must never contain "
    "the words rectangle, box, outline, guide, map, marker, diagram, "
    "framework, collage, panel, split-screen or diptych, or any color name "
    "used to describe a placement region. Placement colors exist only in "
    "the user's notes to you; they are not part of the image and must not "
    "be named.\n"
    "2. Open by declaring the image a RAW, clean photograph of ONE single "
    "continuous environment with no graphics, overlays, text, labels, or "
    "colored lines: one camera viewpoint, one continuous floor and wall "
    "behind every figure, one lighting setup. Every subject stands together "
    "inside that same frame - never in separate photos, panels or a split "
    "screen.\n"
    "3. Express every position in plain scene language derived from the "
    "coordinates (e.g. 'in the left third of the frame, standing full body "
    "from floor to near the ceiling line'), plus one 'framed by "
    "x1, y1, x2, y2 on a 0-1000 grid' clause per subject. Coordinates are "
    "invisible framing data for this one photograph, never depicted "
    "objects and never separate frames.\n"
    "4. For each subject: head-to-feet scale cue (fills the vertical extent "
    "of its area), body horizontally centered, physically consistent "
    "contact shadow on the same floor as every other subject.\n"
    "5. Preserve each subject's exact sex, face, hair, skin tone, outfit "
    "and proportions as shown in its reference picture; name those "
    "attributes from the reference, never substitute generic words like "
    "'person'; require one shared lighting setup consistent with the room.\n"
    "6. Never write negative instructions like 'must NOT show the box'; "
    "state positively that only people, room and light exist in the frame.\n"
    "7. The user brief adds scene-level context only (lighting, mood, "
    "background elements); it must never change a subject's pose, position, "
    "scale or identity given per subject. If the brief seems to contradict "
    "a subject sentence, the subject sentence wins.\n"
    "8. Output only the final prompt text, no preamble, no markdown."
)

COORDS_LINE = (
    "The subject positions below are given as x1, y1, x2, y2 coordinates on "
    "a 0-1000 normalized grid covering the full frame, origin at the top-left "
    "corner; they describe where each subject physically stands or sits in "
    "the room of this one photograph.\n\n"
)

SKELETON_TAIL = (
    "\n\nAll figures share one lighting setup with the scene, matching shadow "
    "direction and color temperature, photorealistic skin and fabric detail, "
    "natural scale against furniture and walls. All figures stand on the same "
    "continuous floor of that one environment, sharing its single light and "
    "one perspective. The finished frame is an ordinary photograph: only "
    "people, environment and light, nothing else. No axes, no numbers, no "
    "frames or panels around anything."
)

IDENTITY_CLAUSE = (
    " Keep that subject's exact sex, face, hair, skin tone, clothing and "
    "proportions identical to how she or he appears in the reference picture."
)

LEGACY_SCALE_FULL = (
    "The subject of this region occupies it fully: head near the top edge, "
    "feet near the bottom edge, body horizontally centered, with a "
    "physically consistent shadow beneath it."
)
LEGACY_SCALE_SPOT = "The subject is placed at this spot in the scene."


def _band_phrase(x1, x2):
    """Horizontal placement in plain scene language (coords 0-1000)."""
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


def _width_phrase(x1, x2):
    """Horizontal extent as a worded share of the frame (coords 0-1000)."""
    w = x2 - x1
    if w >= 400:
        return "a broad area"
    if w >= 250:
        return "a moderate area"
    return "a narrow area"


def _vertical_phrase(y1, y2):
    """Vertical extent as a body-scale cue (coords 0-1000)."""
    h = y2 - y1
    if h >= 450:
        return "stands full body from the floor to near the top of the frame"
    if h >= 220:
        return "stands from the floor to about mid-frame height"
    return "is placed at that spot in the scene"


def _relative_phrase(gap, prev_ref):
    """Spacing to the neighbour on the left, in scene language."""
    who = "the subject of <Picture {}>".format(prev_ref)
    if gap < 0:
        return ", in the same area as {}, slightly to its right".format(who)
    if gap < 60:
        return ", immediately to the right of {}, almost shoulder to " \
               "shoulder".format(who)
    if gap < 160:
        return ", close to the right of {}".format(who)
    return ", well separated to the right of {}".format(who)


def _resolved_regions(boxes, first_ref=2):
    """Left-to-right region order. Each box becomes a dict carrying its
    reference slot, subject wording (box description if typed, else an
    identity-preserving binding to <Picture N>), and the bbox translated
    into proportional scene language plus raw coords for legacy modes."""
    ordered = sorted(boxes, key=lambda x: x["bbox_px"][0] if x["bbox_px"] else 0)
    out = []
    prev = None
    for i, b in enumerate(ordered):
        ref = first_ref + i
        desc = b["desc"].strip()
        x1, y1, x2, y2 = b["bbox_norm"]
        r = {
            "b": b,
            "ref": ref,
            "desc": desc,
            "subject": desc or ("the exact subject shown in <Picture {}>"
                                .format(ref)),
            "band": _band_phrase(x1, x2),
            "width": _width_phrase(x1, x2),
            "vertical": _vertical_phrase(y1, y2),
            "relative": (_relative_phrase(x1 - prev["x2"], prev["ref"])
                         if prev else ""),
            "coords": "{}, {}, {}, {}".format(x1, y1, x2, y2),
            "full_height": (y2 - y1) >= 150,
        }
        out.append(r)
        prev = {"x2": x2, "ref": ref}
    return out


def build_template_prompt(boxes, roles="", extra="", first_ref=2, env_ref=1,
                          coord_mode="relational"):
    head = (
        "RAW candid photograph, untouched camera look, no graphics, no "
        "overlays, no text, no labels, no colored lines"
        + (", no coordinate axes and no numbers"
           if coord_mode == "relational" else "")
        + ". The finished frame is one single continuous "
        "photograph of the environment shown in <Picture {0}>: its entire "
        "setting - floor, structures, background and lighting - remains "
        "exactly as in that picture, captured from one camera viewpoint with "
        "one continuous floor and one lighting setup. Every subject below "
        "stands together inside this same environment and this same frame - "
        "never in separate panels, side-by-side images or a split "
        "screen.\n\n".format(env_ref))
    parts = [head]
    if roles.strip():
        parts.append(roles.strip() + "\n\n")
    if coord_mode != "relational":
        parts.append(COORDS_LINE)
    lines = []
    for r in _resolved_regions(boxes, first_ref):
        label = "{:02d}".format(r["b"]["label"])
        identity = "" if r["desc"] else IDENTITY_CLAUSE
        if coord_mode == "coords":
            lines.append(
                "Region {}: coordinates [{}], {}. {} Subject and action: "
                "{}.{}".format(label, r["coords"], r["band"],
                               LEGACY_SCALE_FULL if r["full_height"]
                               else LEGACY_SCALE_SPOT,
                               r["subject"], identity))
            continue
        line = ("Region {}, {}: {}, occupying {} of the frame width{}. "
                "Subject and action: {}.{}".format(
                    label, r["band"], r["vertical"], r["width"],
                    r["relative"], r["subject"], identity))
        if coord_mode == "both":
            line += " Coordinates on the 0-1000 grid: [{}].".format(r["coords"])
        lines.append(line)
    parts.append("\n".join(lines))
    if extra.strip():
        # scene-level scoping: an unscoped brief with per-subject verbs
        # ("subject 1 and 2 are performing") overrode the per-region poses
        parts.append(
            "\n\nAdditional brief (scene-level only - lighting, mood, "
            "background elements; it must not change any subject's pose, "
            "position, scale or identity stated above): " + extra.strip())
    parts.append(SKELETON_TAIL)
    return "".join(parts)


def build_llm_request(boxes, roles="", extra="", first_ref=2, env_ref=1,
                      coord_mode="relational"):
    """User-message payload for the connected Qwen (or for core Generate Text)."""
    lines = ["Layout job for MiniMax-H3-Image.", "",
             "References:",
             "- <Picture {0}>: the CLEAN environment; the base photograph of "
             "the whole frame.".format(env_ref)]
    for r in _resolved_regions(boxes, first_ref):
        label = "{:02d}".format(r["b"]["label"])
        if coord_mode == "coords":
            place = "coords=[{}]".format(r["coords"])
        else:
            place = ("{}, {}, occupying {} of the frame width{}".format(
                r["vertical"], r["band"], r["width"], r["relative"]))
            if coord_mode == "both":
                place += ", coords=[{}]".format(r["coords"])
        lines.append(
            "- region {} = <Picture {}>: placement: {}. Subject: {}{}".format(
                label, r["ref"], place, r["subject"],
                "" if r["desc"] else " (describe them from the reference "
                                     "image)"))
    if roles.strip():
        lines += ["", "Reference picture roles:", roles.strip()]
    if extra.strip():
        lines += ["",
                  "User brief (scene-level only - lighting, mood, background; "
                  "never overrides the per-subject placement or pose above):",
                  extra.strip()]
    if coord_mode == "relational":
        lines += ["",
                  "Write ONE continuous prompt paragraph: a RAW single-"
                  "environment opening naming the environment reference as "
                  "the base of the whole frame, one placement sentence per "
                  "subject using the proportional hints, identity "
                  "preservation from each reference, then a shared-light "
                  "closing on the same continuous floor. No numbers, no "
                  "axes, no guide words."]
    else:
        lines += ["",
                  "Write ONE continuous prompt paragraph: a RAW single-"
                  "environment opening, one placement sentence per subject "
                  "carrying its coordinates verbatim on the 0-1000 grid, "
                  "identity preservation from each reference, then a shared-"
                  "light closing on the same continuous floor. All regions "
                  "belong to ONE photograph - never separate panels. No "
                  "guide words."]
    return "\n".join(lines)
