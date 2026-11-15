"""Pure-python core logic for H3-LayoutKit: box state parsing, color naming,
preview rendering, and prompt assembly. No torch / no comfy imports so it can
be unit-tested standalone.
"""

import json

NAMED_COLORS = {
    "red": (229, 57, 53), "green": (67, 160, 71), "blue": (30, 136, 229),
    "orange": (251, 140, 0), "purple": (142, 36, 170), "cyan": (0, 188, 212),
    "yellow": (253, 216, 53), "pink": (236, 64, 122), "white": (245, 245, 245),
    "black": (20, 20, 20),
}


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


def color_name(rgb):
    best, dist = "red", None
    for name, ref in NAMED_COLORS.items():
        d = sum((a - c) ** 2 for a, c in zip(rgb, ref))
        if dist is None or d < dist:
            best, dist = name, d
    return best


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

SYSTEM_PROMPT = (
    "You are a prompt writer for MiniMax-H3-Image, a 6-step distilled "
    "image-editing model conditioned on reference pictures labeled "
    "<Picture 1>, <Picture 2>, ... and a text prompt. The user gives you a "
    "layout job: reference images, and region placements given as "
    "coordinates. Write ONE final prompt paragraph set for H3-Image.\n"
    "Rules:\n"
    "1. Describe ONLY the final photograph. The output must never contain "
    "the words rectangle, box, outline, guide, map, marker, diagram, "
    "framework, or any color name used to describe a placement region. "
    "Placement colors exist only in the user's notes to you; they are not "
    "part of the image and must not be named.\n"
    "2. Express every position in plain scene language derived from the "
    "coordinates (e.g. 'in the left third of the frame, standing full body "
    "from floor to near the ceiling line'), plus optionally one 'framed by "
    "x1, y1, x2, y2 on a 0-1000 grid' clause per subject. Coordinates are "
    "invisible framing data, never depicted objects.\n"
    "3. Open by declaring the image a RAW, clean photograph with no "
    "graphics, overlays, text, labels, or colored lines.\n"
    "4. For each subject: head-to-feet scale cue (fills the vertical extent "
    "of its area), body horizontally centered, physically consistent "
    "contact shadow.\n"
    "5. Preserve each subject's exact sex, face, hair, skin tone, outfit "
    "and proportions as shown in its reference picture; name those "
    "attributes from the reference, never substitute generic words like "
    "'person'; require one shared lighting setup consistent with the room.\n"
    "6. Never write negative instructions like 'must NOT show the box'; "
    "state positively that only people, room and light exist in the frame.\n"
    "7. Output only the final prompt text, no preamble, no markdown."
)

SKELETON_HEAD = (
    "RAW candid photograph, untouched camera look, no graphics, no overlays, "
    "no text, no labels, no colored lines. The finished frame is simply the "
    "described scene with its people, exactly as if shot in one take.\n\n"
)

MAP_LINE = (
    "<Picture 1> is an invisible placement map for this photograph - transfer "
    "only the positions, sizes and left-right order of its marked regions "
    "into real-world space, and render nothing of the map itself. Coordinates "
    "are x1, y1, x2, y2 on a 0-1000 normalized grid, origin top-left.\n\n"
)

COORDS_LINE = (
    "The subject positions below are given as x1, y1, x2, y2 coordinates on "
    "a 0-1000 normalized grid covering the full frame, origin at the top-left "
    "corner; they describe where each subject physically stands or sits in "
    "the room.\n\n"
)

SKELETON_TAIL = (
    "\n\nAll figures share one lighting setup with the scene, matching shadow "
    "direction and color temperature, photorealistic skin and fabric detail, "
    "natural scale against furniture and walls. The guide's rectangles, number "
    "tags and colors belong only to the guide and physically cannot appear in "
    "a photograph."
)

SKELETON_TAIL_COORDS = (
    "\n\nAll figures share one lighting setup with the scene, matching shadow "
    "direction and color temperature, photorealistic skin and fabric detail, "
    "natural scale against furniture and walls. The finished frame is an "
    "ordinary photograph: only people, room and light, nothing else."
)

IDENTITY_CLAUSE = (
    " Keep that subject's exact sex, face, hair, skin tone, clothing and "
    "proportions identical to how she or he appears in the reference picture."
)


def _resolved_regions(boxes, first_ref=2):
    """Left-to-right region order; subject = box description if typed, else
    an identity-preserving binding to the matching reference slot
    (<Picture 2>, <Picture 3>, ...). Never the word 'person' alone."""
    ordered = sorted(boxes, key=lambda x: x["bbox_px"][0] if x["bbox_px"] else 0)
    out = []
    for i, b in enumerate(ordered):
        desc = b["desc"].strip()
        if desc:
            subject = desc
        else:
            subject = ("the exact subject shown in <Picture {}>".format(
                first_ref + i))
        x1, y1, x2, y2 = b["bbox_norm"]
        full_height = (y2 - y1) >= 150  # >=15% of frame: head-to-feet is meaningful
        out.append((b, subject, full_height, bool(desc)))
    return out


def build_template_prompt(boxes, roles="", extra="", map_image=True, first_ref=2):
    lines = []
    for b, subject, full_height, has_desc in _resolved_regions(boxes, first_ref):
        cname = color_name(hex_to_rgb(b["hex"]))
        x1, y1, x2, y2 = b["bbox_norm"]
        scale = ("The subject of this region occupies it fully: head near the "
                 "top edge, feet near the bottom edge, body horizontally "
                 "centered, with a physically consistent shadow beneath it."
                 if full_height else
                 "The subject is placed at this spot in the scene.")
        line = ("Region {}: ".format("{:02d}".format(b["label"]))
                + ("color {}, ".format(cname) if map_image else "")
                + "coordinates [{}, {}, {}, {}]. {} Subject and action: {}.{}"
                .format(x1, y1, x2, y2, scale, subject,
                        IDENTITY_CLAUSE if not has_desc else ""))
        lines.append(line)
    parts = [SKELETON_HEAD]
    if roles.strip():
        parts.append(roles.strip() + "\n\n")
    parts.append(MAP_LINE if map_image else COORDS_LINE)
    parts.append("\n".join(lines))
    if extra.strip():
        parts.append("\n\nAdditional brief: " + extra.strip())
    parts.append(SKELETON_TAIL if map_image else SKELETON_TAIL_COORDS)
    return "".join(parts)


def build_llm_request(boxes, roles="", extra="", first_ref=2):
    """User-message payload for the connected Qwen (or for core Generate Text)."""
    lines = ["Layout job for MiniMax-H3-Image.", ""]
    if roles.strip():
        lines += ["Reference picture roles:", roles.strip(), ""]
    lines.append("Placement regions (left to right). Each region's subject is")
    lines.append("resolved to a reference picture or user description:")
    for b, subject, full_height, has_desc in _resolved_regions(boxes, first_ref):
        cname = color_name(hex_to_rgb(b["hex"]))
        lines.append(
            "- region {} color={} coords={} subject={}{}{}".format(
                "{:02d}".format(b["label"]), cname,
                ",".join(str(v) for v in b["bbox_norm"]),
                repr(subject),
                "" if has_desc else " (describe them from the reference image)",
                " fill=yes" if b["fill"] else "",
            ))
    if extra.strip():
        lines += ["", "User brief:", extra.strip()]
    return "\n".join(lines)
