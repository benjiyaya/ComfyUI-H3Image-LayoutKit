// H3 BBox Canvas — hardened box editor for the classic (Nodes 1.0) frontend.
// Interaction grammar follows KJNodes' PointsEditor (kijai, GPL-3.0): corner->
// edge->interior hit priority, grab-offset resize, mode cursors, right-click
// delete. Implementation is original; multi-box + swatch/desc toolbar added.
//
// - click with <3px travel = pure SELECT (no accidental move/resize)
// - 12px zoom-compensated hit radius; selected box gets 8 visible handles
// - swatch row always active; applies to selection, or next-box default
// - hover cursors, right-click delete, Esc deselect, dblclick -> description
// - wheel events pass through to the graph canvas
// - ResizeObserver suppressed mid-drag

import { app } from "../../scripts/app.js";

const WIDGET_ID = "editor_state";
const SEL_RADIUS = 12;   // css px for handles/edges
const DRAG_THRESH = 3;   // px of travel before a click becomes a drag
const SWATCHES = ["#e53935", "#fb8c00", "#fdd835", "#43a047", "#00bcd4",
                  "#1e88e5", "#8e24aa", "#ec407a", "#f5f5f5", "#141414"];
const NAMED = { red: "#e53935", orange: "#fb8c00", yellow: "#fdd835",
                green: "#43a047", cyan: "#00bcd4", blue: "#1e88e5",
                purple: "#8e24aa", pink: "#ec407a", white: "#f5f5f5",
                black: "#141414" };

function colorName(hex) {
    const p = (hex || "").replace("#", "");
    if (p.length !== 6) return "red";
    const c = [0, 2, 4].map((i) => parseInt(p.substr(i, 2), 16));
    let best = "red", bd = Infinity;
    for (const [n, h] of Object.entries(NAMED)) {
        const q = h.replace("#", "");
        const r = [0, 2, 4].map((i) => parseInt(q.substr(i, 2), 16));
        const d = r.reduce((s, v, i) => s + (v - c[i]) ** 2, 0);
        if (d < bd) { bd = d; best = n; }
    }
    return best;
}

function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }

// KJ rectHitTest semantics: corners, then edges, then interior.
function rectHit(mx, my, b, radX, radY) {
    const x1 = b.x, y1 = b.y, x2 = b.x + b.w, y2 = b.y + b.h;
    const hit = (cx, cy) => Math.abs(mx - cx) < radX && Math.abs(my - cy) < radY;
    if (hit(x1, y1)) return "resize-nw";
    if (hit(x2, y1)) return "resize-ne";
    if (hit(x1, y2)) return "resize-sw";
    if (hit(x2, y2)) return "resize-se";
    if (mx >= x1 - radX && mx <= x2 + radX && Math.abs(my - y1) < radY) return "resize-n";
    if (mx >= x1 - radX && mx <= x2 + radX && Math.abs(my - y2) < radY) return "resize-s";
    if (my >= y1 - radY && my <= y2 + radY && Math.abs(mx - x1) < radX) return "resize-w";
    if (my >= y1 - radY && my <= y2 + radY && Math.abs(mx - x2) < radX) return "resize-e";
    if (mx >= x1 && mx <= x2 && my >= y1 && my <= y2) return "move";
    return null;
}

function cursorFor(mode) {
    return { move: "move", "resize-nw": "nwse-resize", "resize-se": "nwse-resize",
             "resize-ne": "nesw-resize", "resize-sw": "nesw-resize",
             "resize-n": "ns-resize", "resize-s": "ns-resize",
             "resize-w": "ew-resize", "resize-e": "ew-resize" }[mode] || null;
}

function relabel(boxes) {
    boxes.slice().sort((a, b) => a.x - b.x).forEach((b, i) => { b.label = i + 1; });
}

function makeEditor(node) {
    const boxes = [];
    let sel = null;
    let nextColor = SWATCHES[0];
    let drag = null;

    const wrap = document.createElement("div");
    wrap.style.cssText = "padding:4px 6px;font-family:inherit;user-select:none;outline:none;";
    wrap.tabIndex = 0;

    // ---- swatch row ----
    const swRow = document.createElement("div");
    swRow.style.cssText = "display:flex;gap:4px;align-items:center;margin-bottom:4px;flex-wrap:wrap;";
    wrap.appendChild(swRow);
    const swBtns = SWATCHES.map((hex) => {
        const s = document.createElement("button");
        s.title = colorName(hex);
        s.style.cssText = `width:18px;height:18px;border-radius:4px;border:1px solid #666;background:${hex};cursor:pointer;padding:0;`;
        s.addEventListener("pointerdown", (e) => e.stopPropagation());
        s.addEventListener("click", (e) => {
            e.stopPropagation();
            nextColor = hex;
            if (sel !== null && boxes[sel]) boxes[sel].color = hex;
            sync();
        });
        swRow.appendChild(s);
        return s;
    });
    const picker = document.createElement("input");
    picker.type = "color"; picker.value = nextColor;
    picker.title = "Custom color (selected box, or next box if none selected)";
    picker.style.cssText = "width:26px;height:20px;border:none;background:none;padding:0;cursor:pointer;";
    picker.addEventListener("pointerdown", (e) => e.stopPropagation());
    picker.addEventListener("input", () => {
        nextColor = picker.value;
        if (sel !== null && boxes[sel]) boxes[sel].color = picker.value;
        sync();
    });
    swRow.appendChild(picker);
    const hint = document.createElement("span");
    hint.style.cssText = "font-size:11px;color:#999;flex:1;";
    swRow.appendChild(hint);

    // ---- desc + actions ----
    const bar = document.createElement("div");
    bar.style.cssText = "display:flex;gap:6px;align-items:center;margin-bottom:4px;";
    wrap.appendChild(bar);
    const descInput = document.createElement("input");
    descInput.placeholder = "description (click a box first)";
    descInput.style.cssText = "flex:1;min-width:100px;padding:2px 6px;font-size:12px;" +
        "background:#1b1b22;color:#eee;border:1px solid #555;border-radius:4px;";
    descInput.addEventListener("pointerdown", (e) => e.stopPropagation());
    const commitDesc = () => { if (sel !== null && boxes[sel]) { boxes[sel].desc = descInput.value; sync(); } };
    descInput.addEventListener("change", commitDesc);
    descInput.addEventListener("blur", commitDesc);
    descInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { commitDesc(); descInput.blur(); }
        e.stopPropagation();
    });
    bar.appendChild(descInput);
    const mkBtn = (label, title, fn) => {
        const b = document.createElement("button");
        b.textContent = label; b.title = title;
        b.style.cssText = "padding:2px 8px;border-radius:4px;border:1px solid #555;" +
            "background:#2a2a33;color:#eee;cursor:pointer;font-size:12px;";
        b.addEventListener("pointerdown", (e) => e.stopPropagation());
        b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
        bar.appendChild(b);
        return b;
    };
    mkBtn("fill", "Toggle translucent fill (selected box)", () => {
        if (sel !== null && boxes[sel]) { boxes[sel].fill = !boxes[sel].fill; sync(); }
    });
    mkBtn("del", "Delete selected box", () => {
        if (sel !== null) { boxes.splice(sel, 1); sel = null; relabel(boxes); sync(); }
    });
    mkBtn("clear", "Remove all boxes", () => { boxes.length = 0; sel = null; sync(); });

    // ---- canvas ----
    const cv = document.createElement("canvas");
    cv.style.cssText = "width:100%;display:block;background:#101014;border:1px solid #333;" +
        "border-radius:4px;touch-action:none;";
    wrap.appendChild(cv);
    const ctx = cv.getContext("2d");

    const gridStep = () => {
        const w = node.widgets?.find((x) => x.name === "width")?.value || 1376;
        const h = node.widgets?.find((x) => x.name === "height")?.value || 768;
        return clamp(h / w, 0.2, 3);
    };

    function fit() {
        if (drag) return;                       // never reflow mid-drag
        const cssW = Math.max(140, wrap.clientWidth - 12);
        const cssH = clamp(cssW * gridStep(), 80, 420);
        const dpr = window.devicePixelRatio || 1;
        const needW = Math.round(cssW * dpr), needH = Math.round(cssH * dpr);
        if (cv.width !== needW || cv.height !== needH || cv.style.height !== cssH + "px") {
            cv.style.height = cssH + "px";
            cv.width = needW; cv.height = needH;
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        }
        render();
    }

    const norm = (e) => {
        const r = cv.getBoundingClientRect();
        return { x: clamp((e.clientX - r.left) / r.width, 0, 1),
                 y: clamp((e.clientY - r.top) / r.height, 0, 1),
                 w: r.width, h: r.height };
    };

    // selected box first (full handle zone), then any other box (half zone)
    function pick(p, radCss) {
        const radX = radCss / p.w, radY = radCss / p.h;
        if (sel !== null && boxes[sel]) {
            const m = rectHit(p.x, p.y, boxes[sel], radX, radY);
            if (m) return { i: sel, mode: m };
        }
        for (let i = boxes.length - 1; i >= 0; i--) {
            if (i === sel) continue;
            const m = rectHit(p.x, p.y, boxes[i], radX / 2, radY / 2);
            if (m) return { i, mode: m === "move" ? "move" : "select" };
        }
        return null;
    }

    cv.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;                     // right button via contextmenu
        e.preventDefault(); e.stopPropagation();
        wrap.focus({ preventScroll: true });
        cv.setPointerCapture(e.pointerId);
        const p = norm(e);
        const h = pick(p, SEL_RADIUS);
        if (h) {
            sel = h.i;
            const b = boxes[sel];
            const mode = (h.mode === "select") ? "move" : h.mode;
            drag = { kind: mode === "move" ? "move" : "resize", mode,
                     start: p, moved: false, orig: { x: b.x, y: b.y, w: b.w, h: b.h } };
        } else {
            const b = { x: p.x, y: p.y, w: 0, h: 0, label: boxes.length + 1,
                        color: nextColor, desc: "", fill: false };
            boxes.push(b);
            sel = boxes.length - 1;
            drag = { kind: "draw", start: p, moved: false };
        }
        refreshToolbar(); render();
    });

    cv.addEventListener("pointermove", (e) => {
        const p = norm(e);
        if (!drag) {
            const h = pick(p, SEL_RADIUS);
            cv.style.cursor = (h && cursorFor(h.mode)) || "crosshair";
            return;
        }
        const distPx = Math.hypot((p.x - drag.start.x) * p.w, (p.y - drag.start.y) * p.h);
        if (!drag.moved && distPx < DRAG_THRESH) return;   // pure click => stay selected
        drag.moved = true;
        const b = boxes[sel];
        if (drag.kind === "draw") {
            b.x = Math.min(drag.start.x, p.x); b.y = Math.min(drag.start.y, p.y);
            b.w = Math.abs(p.x - drag.start.x); b.h = Math.abs(p.y - drag.start.y);
        } else if (drag.kind === "move") {
            const o = drag.orig;
            b.x = clamp(o.x + (p.x - drag.start.x), 0, 1 - o.w);
            b.y = clamp(o.y + (p.y - drag.start.y), 0, 1 - o.h);
        } else {
            const o = drag.orig;
            let x1 = o.x, y1 = o.y, x2 = o.x + o.w, y2 = o.y + o.h;
            if (drag.mode.includes("w")) x1 = clamp(p.x, 0, x2 - 0.01);
            if (drag.mode.includes("e")) x2 = clamp(p.x, x1 + 0.01, 1);
            if (drag.mode.includes("n")) y1 = clamp(p.y, 0, y2 - 0.01);
            if (drag.mode.includes("s")) y2 = clamp(p.y, y1 + 0.01, 1);
            b.x = x1; b.y = y1; b.w = x2 - x1; b.h = y2 - y1;
        }
        render();
    });

    const endDrag = () => {
        if (!drag) return;
        const b = boxes[sel];
        if (drag.kind === "draw" && (!drag.moved || b.w < 0.02 || b.h < 0.02)) {
            boxes.splice(sel, 1);               // stray click on empty = deselect
            sel = null;
        } else if (drag.moved) {
            const dragged = b;
            relabel(boxes);
            sel = boxes.indexOf(dragged);
        }
        drag = null; refreshToolbar(); sync();
    };
    cv.addEventListener("pointerup", endDrag);
    cv.addEventListener("pointercancel", endDrag);

    cv.addEventListener("contextmenu", (e) => {  // KJ pattern: right-click deletes
        e.preventDefault(); e.stopPropagation();
        const h = pick(norm(e), SEL_RADIUS);
        if (h) {
            boxes.splice(h.i, 1);
            if (sel === h.i) sel = null; else if (sel > h.i) sel--;
            relabel(boxes); sync();
        }
    });

    cv.addEventListener("dblclick", (e) => {
        const h = pick(norm(e), SEL_RADIUS);
        if (h) { sel = h.i; sync(); descInput.focus(); descInput.select(); }
    });

    wrap.addEventListener("keydown", (e) => {
        if (e.target.tagName === "INPUT") return;
        if ((e.key === "Delete" || e.key === "Backspace") && sel !== null) {
            boxes.splice(sel, 1); sel = null; relabel(boxes); sync();
        } else if (e.key === "Escape") { sel = null; refreshToolbar(); render(); }
    });

    // graph zoom passthrough while hovering the widget
    cv.addEventListener("wheel", (e) => {
        const gc = document.getElementById("graph-canvas");
        if (gc) gc.dispatchEvent(new WheelEvent(e.type, e));
    }, { passive: true });

    function refreshToolbar() {
        const b = sel !== null ? boxes[sel] : null;
        descInput.disabled = !b;
        descInput.value = b ? (b.desc || "") : "";
        descInput.placeholder = b ? `description — box ${String(b.label).padStart(2, "0")}`
                                  : "description (click a box first)";
        hint.textContent = b ? `selected ${String(b.label).padStart(2, "0")} · ${colorName(b.color)}${b.fill ? " · filled" : ""}`
                             : "nothing selected — colors apply to the next box";
        const cur = ((b ? b.color : nextColor) || "").toLowerCase();
        swBtns.forEach((btn, i) => {
            btn.style.outline = SWATCHES[i].toLowerCase() === cur ? "2px solid #fff" : "none";
        });
        if (/^#[0-9a-f]{6}$/i.test(cur)) picker.value = cur;
    }

    function render() {
        const r = cv.getBoundingClientRect();
        ctx.clearRect(0, 0, r.width, r.height);
        ctx.fillStyle = "#101014"; ctx.fillRect(0, 0, r.width, r.height);
        ctx.strokeStyle = "#26262e"; ctx.lineWidth = 1;
        for (let i = 1; i < 8; i++) {
            ctx.beginPath(); ctx.moveTo((r.width * i) / 8, 0); ctx.lineTo((r.width * i) / 8, r.height); ctx.stroke();
            ctx.beginPath(); ctx.moveTo(0, (r.height * i) / 8); ctx.lineTo(r.width, (r.height * i) / 8); ctx.stroke();
        }
        boxes.forEach((b, i) => {
            const x = b.x * r.width, y = b.y * r.height, w = b.w * r.width, h = b.h * r.height;
            const isSel = i === sel;
            if (b.fill) {
                ctx.globalAlpha = 0.18; ctx.fillStyle = b.color; ctx.fillRect(x, y, w, h); ctx.globalAlpha = 1;
            }
            ctx.strokeStyle = b.color; ctx.lineWidth = isSel ? 3 : 2;
            ctx.strokeRect(x, y, w, h);
            if (isSel) {
                ctx.setLineDash([4, 4]); ctx.strokeStyle = "rgba(255,255,255,0.65)"; ctx.lineWidth = 1;
                if (w > 8 && h > 8) ctx.strokeRect(x + 3, y + 3, w - 6, h - 6);
                ctx.setLineDash([]);
                ctx.fillStyle = b.color; ctx.strokeStyle = "#111"; ctx.lineWidth = 1;
                for (const [hx, hy] of [[x, y], [x + w / 2, y], [x + w, y], [x + w, y + h / 2],
                                        [x + w, y + h], [x + w / 2, y + h], [x, y + h], [x, y + h / 2]]) {
                    ctx.fillRect(hx - 5, hy - 5, 10, 10);
                    ctx.strokeRect(hx - 5, hy - 5, 10, 10);
                }
            }
            const tag = String(b.label).padStart(2, "0") + " " + colorName(b.color);
            ctx.font = "11px sans-serif";
            const tw = ctx.measureText(tag).width + 10;
            const ty = y > 16 ? y - 15 : y;
            ctx.fillStyle = b.color; ctx.fillRect(x, ty, tw, 15);
            ctx.fillStyle = "#111"; ctx.fillText(tag, x + 5, ty + 11);
        });
        if (!boxes.length) {
            ctx.fillStyle = "#666"; ctx.font = "12px sans-serif";
            ctx.fillText("drag: new box · click: select · handles: resize · right-click: delete", 12, 24);
        }
    }

    function sync() {
        const sw = (node.widgets || []).find((w) => w.name === WIDGET_ID);
        if (sw) sw.setValue(JSON.stringify(boxes), true, false);
        node.setDirtyCanvas(true);
        refreshToolbar(); render();
    }

    function loadState() {
        const sw = (node.widgets || []).find((w) => w.name === WIDGET_ID);
        if (!sw || !sw.value) return;
        try {
            const parsed = JSON.parse(sw.value);
            if (Array.isArray(parsed)) { boxes.length = 0; boxes.push(...parsed); }
        } catch (err) { /* keep empty */ }
        sel = null; refreshToolbar(); fit();
    }

    node._h3loadState = loadState;
    requestAnimationFrame(() => { fit(); loadState(); });
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(fit).observe(wrap);
    return wrap;
}

app.registerExtension({
    name: "H3.Layout.BBoxCanvas",
    async beforeRegisterNodeDef(nodeType, nodeData) {
        if (nodeData.name !== "H3BBoxCanvas") return;
        const onCreated = nodeType.prototype.onNodeCreated;
        nodeType.prototype.onNodeCreated = function () {
            const r = onCreated ? onCreated.apply(this, arguments) : undefined;
            this.addDOMWidget("h3bbox", "div", makeEditor(this), { serialize: false });
            this.setSize(this.computeSize());
            return r;
        };
        const onConfigure = nodeType.prototype.onConfigure;
        nodeType.prototype.onConfigure = function (info) {
            const res = onConfigure ? onConfigure.apply(this, arguments) : undefined;
            if (this._h3loadState) requestAnimationFrame(() => this._h3loadState());
            return res;
        };
    },
});
