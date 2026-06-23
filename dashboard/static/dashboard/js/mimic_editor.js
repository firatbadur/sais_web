/* ===========================================================================
 * mimic_editor.js — SCADA/HMI Mimik Tasarım Editörü (Fabric.js)
 * ---------------------------------------------------------------------------
 * Tam ekran standalone editör mantığı. window.MIMIC_CONFIG ile yapılandırılır:
 *   { screenId, urls:{save,get,list,delete,viewer,editorNew}, csrf, initial }
 * Bağımlılıklar: fabric (5.x), mimic_symbols.js, mimic_runtime.js.
 * ======================================================================== */
(function () {
    "use strict";

    var CFG = window.MIMIC_CONFIG || {};
    var $ = function (id) { return document.getElementById(id); };
    var GRID = 20;

    // ----------------------------------------------------------------- //
    // Canvas kurulumu
    // ----------------------------------------------------------------- //
    var canvas = new fabric.Canvas("mimic-canvas", {
        backgroundColor: "#f5f8fa",
        preserveObjectStacking: true,
        selection: true,
        fireRightClick: true,
        stopContextMenu: true,
        fireMiddleClick: true
    });
    fabric.Object.prototype.cornerColor = "#009ef7";
    fabric.Object.prototype.cornerStyle = "circle";
    fabric.Object.prototype.borderColor = "#009ef7";
    fabric.Object.prototype.cornerSize = 9;
    fabric.Object.prototype.transparentCorners = false;
    fabric.Object.prototype.objectCaching = false;

    var state = {
        screenId: CFG.screenId || null,
        name: (CFG.initial && CFG.initial.name) || "",
        width: (CFG.initial && CFG.initial.width) || 1280,
        height: (CFG.initial && CFG.initial.height) || 720,
        background: (CFG.initial && CFG.initial.background) || "#f5f8fa",
        snap: true, grid: true, dirty: false,
        zoom: 1
    };

    var runtime = window.MimicRuntime(canvas);
    var boundary = null;

    // ----------------------------------------------------------------- //
    // Grid (viewport-aware pattern background)
    // ----------------------------------------------------------------- //
    function gridTile(size, bg) {
        var t = document.createElement("canvas");
        t.width = t.height = size;
        var c = t.getContext("2d");
        c.fillStyle = bg; c.fillRect(0, 0, size, size);
        c.strokeStyle = "rgba(120,130,145,0.28)";
        c.lineWidth = 1;
        c.beginPath();
        c.moveTo(size - 0.5, 0); c.lineTo(size - 0.5, size);
        c.moveTo(0, size - 0.5); c.lineTo(size, size - 0.5);
        c.stroke();
        return t;
    }
    function applyBackground() {
        if (state.grid) {
            canvas.setBackgroundColor(
                new fabric.Pattern({ source: gridTile(GRID, state.background), repeat: "repeat" }),
                canvas.requestRenderAll.bind(canvas));
        } else {
            canvas.setBackgroundColor(state.background, canvas.requestRenderAll.bind(canvas));
        }
    }

    function rebuildBoundary() {
        if (boundary) canvas.remove(boundary);
        boundary = new fabric.Rect({
            left: 0, top: 0, width: state.width, height: state.height,
            fill: "", stroke: "#009ef7", strokeWidth: 1, strokeDashArray: [6, 4],
            selectable: false, evented: false, excludeFromExport: true,
            isHelper: true, hoverCursor: "default"
        });
        canvas.add(boundary);
        canvas.sendToBack(boundary);
    }

    // ----------------------------------------------------------------- //
    // Zoom / pan
    // ----------------------------------------------------------------- //
    function setZoom(z, point) {
        z = Math.max(0.1, Math.min(5, z));
        state.zoom = z;
        if (point) canvas.zoomToPoint(point, z);
        else canvas.setZoom(z);
        if ($("status-zoom")) $("status-zoom").textContent = Math.round(z * 100) + "%";
        if ($("zoom-label")) $("zoom-label").textContent = Math.round(z * 100) + "%";
        canvas.requestRenderAll();
    }
    function zoomFit() {
        var wrap = $("canvas-wrap");
        var pad = 60;
        var z = Math.min((wrap.clientWidth - pad) / state.width,
                         (wrap.clientHeight - pad) / state.height);
        z = Math.max(0.1, Math.min(2, z));
        canvas.setViewportTransform([z, 0, 0, z, 0, 0]);
        // ortala
        var vpt = canvas.viewportTransform;
        vpt[4] = (wrap.clientWidth - state.width * z) / 2;
        vpt[5] = (wrap.clientHeight - state.height * z) / 2;
        canvas.setViewportTransform(vpt);
        state.zoom = z;
        setZoom(z);
    }

    canvas.on("mouse:wheel", function (opt) {
        var e = opt.e;
        if (e.ctrlKey || true) {
            var delta = e.deltaY;
            var z = canvas.getZoom() * (delta > 0 ? 0.92 : 1.08);
            setZoom(z, { x: e.offsetX, y: e.offsetY });
            e.preventDefault(); e.stopPropagation();
        }
    });

    // Pan: boşlukta sürükle (space) veya orta tuş
    var panning = false, spaceDown = false, lastPan = null;
    canvas.on("mouse:down", function (opt) {
        // Simülasyon modunda butona basılıyorsa → etiket aksiyonu (pan değil)
        if (runtime.isRunning()) {
            if (opt.target && opt.target.isButton) {
                runtime.pressButton(opt.target);
                pulseButton(opt.target);
                refreshSimRow(opt.target.scada && opt.target.scada.tag);
            }
            return;
        }
        if (spaceDown || opt.e.button === 1) {
            panning = true; canvas.selection = false;
            lastPan = { x: opt.e.clientX, y: opt.e.clientY };
            canvas.setCursor("grabbing");
            opt.e.preventDefault();   // orta tuş otomatik-kaydırma imlecini engelle
        }
    });
    canvas.on("mouse:move", function (opt) {
        if (panning && lastPan) {
            var vpt = canvas.viewportTransform;
            vpt[4] += opt.e.clientX - lastPan.x;
            vpt[5] += opt.e.clientY - lastPan.y;
            canvas.requestRenderAll();
            lastPan = { x: opt.e.clientX, y: opt.e.clientY };
        }
        if ($("status-coords") && opt.absolutePointer) {
            $("status-coords").textContent =
                Math.round(opt.absolutePointer.x) + ", " + Math.round(opt.absolutePointer.y);
        }
    });
    canvas.on("mouse:up", function (opt) {
        if (runtime.isRunning() && opt.target && opt.target.isButton) {
            runtime.releaseButton(opt.target);
            unpulseButton(opt.target);
            refreshSimRow(opt.target.scada && opt.target.scada.tag);
            return;
        }
        panning = false; lastPan = null; canvas.selection = true;
    });

    // Buton basma görsel geri bildirimi
    function pulseButton(o) { o._origOp = (o.opacity == null ? 1 : o.opacity); o.set("opacity", 0.7); canvas.requestRenderAll(); }
    function unpulseButton(o) { o.set("opacity", o._origOp == null ? 1 : o._origOp); canvas.requestRenderAll(); }
    function refreshSimRow(tag) {
        if (!tag) return;
        var disp = document.querySelector('[data-val="' + tag + '"]');
        var rng = document.querySelector('input[data-tag="' + tag + '"]');
        var v = runtime.getTags()[tag] || 0;
        if (disp) disp.textContent = Math.round(v);
        if (rng) rng.value = v;
    }

    // ----------------------------------------------------------------- //
    // Snap-to-grid
    // ----------------------------------------------------------------- //
    canvas.on("object:moving", function (opt) {
        if (!state.snap) return;
        var o = opt.target;
        o.set({ left: Math.round(o.left / GRID) * GRID,
                top: Math.round(o.top / GRID) * GRID });
    });

    // ----------------------------------------------------------------- //
    // Undo / redo
    // ----------------------------------------------------------------- //
    var undoStack = [], redoStack = [], suspend = false;
    var SER_PROPS = ["scada", "name", "isHelper", "selectable", "evented", "isButton"];
    function serialize() { return JSON.stringify(canvas.toJSON(SER_PROPS)); }
    function pushUndo() {
        if (suspend) return;
        undoStack.push(serialize());
        if (undoStack.length > 60) undoStack.shift();
        redoStack.length = 0;
        markDirty();
    }
    function restoreFrom(json) {
        suspend = true;
        canvas.loadFromJSON(json, function () {
            // helper (boundary) JSON'a girmez; yeniden kur
            rebuildBoundary();
            applyBackground();
            canvas.requestRenderAll();
            suspend = false;
            refreshLayers();
        });
    }
    function undo() {
        if (undoStack.length <= 1) return;
        redoStack.push(undoStack.pop());
        restoreFrom(undoStack[undoStack.length - 1]);
    }
    function redo() {
        if (!redoStack.length) return;
        var j = redoStack.pop();
        undoStack.push(j);
        restoreFrom(j);
    }
    canvas.on("object:added", function (e) { if (!e.target.isHelper) pushUndo(); });
    canvas.on("object:modified", pushUndo);
    canvas.on("object:removed", function (e) { if (!e.target.isHelper) pushUndo(); });

    function markDirty() {
        state.dirty = true;
        if ($("dirty-dot")) $("dirty-dot").style.display = "inline-block";
    }
    function clearDirty() {
        state.dirty = false;
        if ($("dirty-dot")) $("dirty-dot").style.display = "none";
    }

    // ----------------------------------------------------------------- //
    // Obje ekleme yardımcıları
    // ----------------------------------------------------------------- //
    function centerPoint() {
        var c = canvas.getVpCenter ? canvas.getVpCenter() :
            { x: state.width / 2, y: state.height / 2 };
        return c;
    }
    function addObject(obj, name) {
        obj.name = name || obj.type;
        obj.scada = obj.scada || { tag: "", anim: "none" };
        var c = centerPoint();
        obj.set({ left: Math.round(c.x), top: Math.round(c.y), originX: "center", originY: "center" });
        canvas.add(obj);
        canvas.setActiveObject(obj);
        canvas.requestRenderAll();
        refreshLayers();
    }

    var SHAPES = {
        rect: function () { return new fabric.Rect({ width: 120, height: 70, fill: "#c2ccd6", stroke: "#5e6b7a", strokeWidth: 2, rx: 0 }); },
        roundrect: function () { return new fabric.Rect({ width: 120, height: 70, fill: "#c2ccd6", stroke: "#5e6b7a", strokeWidth: 2, rx: 12, ry: 12 }); },
        circle: function () { return new fabric.Circle({ radius: 45, fill: "#c2ccd6", stroke: "#5e6b7a", strokeWidth: 2 }); },
        ellipse: function () { return new fabric.Ellipse({ rx: 70, ry: 45, fill: "#c2ccd6", stroke: "#5e6b7a", strokeWidth: 2 }); },
        triangle: function () { return new fabric.Triangle({ width: 90, height: 80, fill: "#c2ccd6", stroke: "#5e6b7a", strokeWidth: 2 }); },
        line: function () { return new fabric.Line([0, 0, 140, 0], { stroke: "#5e6b7a", strokeWidth: 4 }); },
        pipe: function () { return new fabric.Line([0, 0, 160, 0], { stroke: "#aeb9c4", strokeWidth: 12, strokeLineCap: "round" }); },
        arrow: function () {
            var line = new fabric.Line([0, 20, 120, 20], { stroke: "#5e6b7a", strokeWidth: 4 });
            var head = new fabric.Triangle({ width: 18, height: 20, fill: "#5e6b7a", left: 120, top: 20, angle: 90, originX: "center", originY: "center" });
            return new fabric.Group([line, head]);
        },
        text: function () { return new fabric.IText("Metin", { fontSize: 26, fill: "#181c32", fontFamily: "Inter, Arial" }); },
        label: function () { return new fabric.Textbox("Etiket", { width: 140, fontSize: 20, fill: "#181c32", fontFamily: "Inter, Arial", textAlign: "center" }); }
    };

    // HMI butonu — yuvarlatılmış kutu + ortalı metin grubu (isButton işaretli).
    // Simülasyon/görüntüleyici modunda tıklanınca bağlı etikete aksiyon uygular.
    function makeButton(opts) {
        opts = opts || {};
        var w = opts.w || 150, h = opts.h || 50;
        var rect = new fabric.Rect({
            width: w, height: h, rx: 8, ry: 8,
            fill: opts.bg || "#009ef7", stroke: opts.stroke || "#0086d4", strokeWidth: 1,
            originX: "center", originY: "center"
        });
        var label = new fabric.Textbox(opts.label || "BUTON", {
            width: w - 16, fontSize: opts.fontSize || 18, fontWeight: "600",
            fill: opts.fg || "#ffffff", textAlign: "center", fontFamily: "Inter, Arial",
            originX: "center", originY: "center", editable: false, splitByGrapheme: false
        });
        var g = new fabric.Group([rect, label], { name: "Buton", subTargetCheck: false });
        g.isButton = true;
        g.scada = { tag: "", anim: "none", action: "toggle",
                    pressValue: 100, releaseValue: 0, setValue: 100,
                    onColor: "#3fbf6f", offColor: "#e4544c", min: 0, max: 100, threshold: 1,
                    speed: 1, unit: "", decimals: 1, moveRange: 60 };
        return g;
    }

    function addShape(kind) {
        if (kind === "button") { addObject(makeButton(), "Buton"); return; }
        var f = SHAPES[kind];
        if (!f) return;
        addObject(f(), kind);
    }

    // Buton alt-parça erişimi
    function btnRect(o) { return o && o._objects ? o._objects[0] : null; }
    function btnLabel(o) { return o && o._objects ? o._objects[1] : null; }

    // SVG sembol ekle
    function addSymbol(sym) {
        fabric.loadSVGFromString(sym.svg, function (objects, options) {
            var grp = fabric.util.groupSVGElements(objects, options);
            grp.scaleToWidth(sym.w);
            grp.set({ name: sym.name });
            grp.scada = { tag: "", anim: "auto", onColor: "#3fbf6f", offColor: "#e4544c", min: 0, max: 100, threshold: 1, speed: 1, unit: "", decimals: 1, moveRange: 60 };
            grp.symbolKey = sym.key;
            var c = centerPoint();
            grp.set({ left: Math.round(c.x), top: Math.round(c.y), originX: "center", originY: "center" });
            canvas.add(grp);
            canvas.setActiveObject(grp);
            canvas.requestRenderAll();
            refreshLayers();
        });
    }

    // Görüntü (PNG/JPG/SVG) ekle
    function addImageFile(file) {
        var reader = new FileReader();
        reader.onload = function (ev) {
            var data = ev.target.result;
            if (/svg/i.test(file.type) || /\.svg$/i.test(file.name)) {
                fabric.loadSVGFromString(atob(data.split(",")[1]), function (objs, opts) {
                    var grp = fabric.util.groupSVGElements(objs, opts);
                    if (grp.width > 400) grp.scaleToWidth(400);
                    addObject(grp, file.name);
                });
            } else {
                fabric.Image.fromURL(data, function (img) {
                    if (img.width > 480) img.scaleToWidth(480);
                    addObject(img, file.name);
                });
            }
        };
        reader.readAsDataURL(file);
    }

    // ----------------------------------------------------------------- //
    // Sembol paleti
    // ----------------------------------------------------------------- //
    function symbolThumb(sym) {
        // SVG'yi data-URL'e çevirip küçük önizleme
        return "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(sym.svg)));
    }
    function buildPalette(filter) {
        var host = $("symbol-list");
        if (!host) return;
        host.innerHTML = "";
        filter = (filter || "").toLowerCase();
        (window.MIMIC_SYMBOLS || []).forEach(function (cat) {
            var items = cat.items.filter(function (s) {
                return !filter || s.name.toLowerCase().indexOf(filter) >= 0;
            });
            if (!items.length) return;
            var sec = document.createElement("div");
            sec.className = "pal-cat";
            sec.innerHTML = '<div class="pal-cat-title">' + cat.label + '</div>';
            var grid = document.createElement("div");
            grid.className = "pal-grid";
            items.forEach(function (s) {
                var cell = document.createElement("div");
                cell.className = "pal-item";
                cell.title = s.name;
                cell.draggable = true;
                cell.innerHTML = '<img src="' + symbolThumb(s) + '" alt=""><span>' + s.name + '</span>';
                cell.addEventListener("click", function () { addSymbol(s); });
                cell.addEventListener("dragstart", function (e) {
                    e.dataTransfer.setData("text/mimic-sym", s.key);
                });
                grid.appendChild(cell);
            });
            sec.appendChild(grid);
            host.appendChild(sec);
        });
    }

    // Drag-drop semboller tuvale
    var wrapEl = $("canvas-wrap");
    if (wrapEl) {
        wrapEl.addEventListener("dragover", function (e) { e.preventDefault(); });
        wrapEl.addEventListener("drop", function (e) {
            e.preventDefault();
            var key = e.dataTransfer.getData("text/mimic-sym");
            if (!key) return;
            var sym = null;
            (window.MIMIC_SYMBOLS || []).forEach(function (c) {
                c.items.forEach(function (s) { if (s.key === key) sym = s; });
            });
            if (sym) {
                var p = canvas.getPointer(e);
                fabric.loadSVGFromString(sym.svg, function (objs, opts) {
                    var grp = fabric.util.groupSVGElements(objs, opts);
                    grp.scaleToWidth(sym.w);
                    grp.set({ left: p.x, top: p.y, originX: "center", originY: "center", name: sym.name });
                    grp.symbolKey = sym.key;
                    grp.scada = { tag: "", anim: "auto", onColor: "#3fbf6f", offColor: "#e4544c", min: 0, max: 100, threshold: 1, speed: 1, unit: "", decimals: 1, moveRange: 60 };
                    canvas.add(grp); canvas.setActiveObject(grp);
                    canvas.requestRenderAll(); refreshLayers();
                });
            }
        });
    }

    // ----------------------------------------------------------------- //
    // Özellik paneli
    // ----------------------------------------------------------------- //
    function activeObj() { return canvas.getActiveObject(); }
    function setVal(id, v) { var el = $(id); if (el && document.activeElement !== el) el.value = (v == null ? "" : v); }

    function syncProps() {
        var o = activeObj();
        var hasSel = !!o;
        document.body.classList.toggle("has-selection", hasSel);
        if (!hasSel) { if ($("status-sel")) $("status-sel").textContent = "—"; return; }
        if ($("status-sel")) $("status-sel").textContent = (o.name || o.type) + (o._objects ? " (grup)" : "");
        setVal("p-x", Math.round(o.left));
        setVal("p-y", Math.round(o.top));
        setVal("p-w", Math.round(o.getScaledWidth()));
        setVal("p-h", Math.round(o.getScaledHeight()));
        setVal("p-angle", Math.round(o.angle || 0));
        setVal("p-opacity", Math.round((o.opacity == null ? 1 : o.opacity) * 100));
        if ($("p-fill")) $("p-fill").value = (typeof o.fill === "string" && o.fill) ? o.fill : "#c2ccd6";
        if ($("p-stroke")) $("p-stroke").value = (typeof o.stroke === "string" && o.stroke) ? o.stroke : "#5e6b7a";
        setVal("p-strokew", o.strokeWidth || 0);
        // text
        var isText = o.type && /text/i.test(o.type);
        document.body.classList.toggle("sel-text", !!isText);
        if (isText) {
            setVal("p-text", o.text);
            setVal("p-fontsize", o.fontSize);
            if ($("p-fontcolor")) $("p-fontcolor").value = (typeof o.fill === "string") ? o.fill : "#181c32";
        }
        // radius
        document.body.classList.toggle("sel-rect", o.type === "rect");
        if (o.type === "rect") setVal("p-radius", o.rx || 0);
        // buton
        var isBtn = !!o.isButton;
        document.body.classList.toggle("sel-button", isBtn);
        if (isBtn) {
            var r = btnRect(o), l = btnLabel(o);
            setVal("bt-label", l ? l.text : "");
            if ($("bt-bg")) $("bt-bg").value = (r && typeof r.fill === "string") ? r.fill : "#009ef7";
            if ($("bt-fg")) $("bt-fg").value = (l && typeof l.fill === "string") ? l.fill : "#ffffff";
            setVal("bt-radius", r ? (r.rx || 0) : 0);
            setVal("bt-fontsize", l ? l.fontSize : 18);
            if ($("bt-action")) $("bt-action").value = (o.scada && o.scada.action) || "toggle";
            setVal("bt-setvalue", (o.scada && o.scada.setValue != null) ? o.scada.setValue : 100);
        }
        syncBindings();
    }

    // Buton özellik güncelleyicileri
    function btnSet(fn) {
        var o = activeObj(); if (!o || !o.isButton) return;
        fn(o, btnRect(o), btnLabel(o));
        o.dirty = true; canvas.requestRenderAll(); markDirty();
    }

    function applyProp(prop, value, isNumber) {
        var o = activeObj(); if (!o) return;
        var v = isNumber ? parseFloat(value) : value;
        if (isNumber && isNaN(v)) return;
        if (prop === "scaleW") {
            o.scaleX = v / (o.width || 1);
        } else if (prop === "scaleH") {
            o.scaleY = v / (o.height || 1);
        } else if (prop === "opacity") {
            o.set("opacity", v / 100);
        } else {
            o.set(prop, v);
        }
        o.setCoords();
        canvas.requestRenderAll();
        pushUndo();
    }

    // Özellik input bağları
    function bindInput(id, fn) { var el = $(id); if (el) el.addEventListener("input", function () { fn(el.value); }); }
    bindInput("p-x", function (v) { applyProp("left", v, true); });
    bindInput("p-y", function (v) { applyProp("top", v, true); });
    bindInput("p-w", function (v) { applyProp("scaleW", v, true); });
    bindInput("p-h", function (v) { applyProp("scaleH", v, true); });
    bindInput("p-angle", function (v) { applyProp("angle", v, true); });
    bindInput("p-opacity", function (v) { applyProp("opacity", v, true); });
    bindInput("p-fill", function (v) { applyProp("fill", v); });
    bindInput("p-stroke", function (v) { applyProp("stroke", v); });
    bindInput("p-strokew", function (v) { applyProp("strokeWidth", v, true); });
    bindInput("p-radius", function (v) { var o = activeObj(); if (o && o.type === "rect") { o.set({ rx: parseFloat(v) || 0, ry: parseFloat(v) || 0 }); canvas.requestRenderAll(); } });
    bindInput("p-text", function (v) { var o = activeObj(); if (o && /text/i.test(o.type)) { o.set("text", v); canvas.requestRenderAll(); } });
    bindInput("p-fontsize", function (v) { var o = activeObj(); if (o && /text/i.test(o.type)) { o.set("fontSize", parseFloat(v) || 12); canvas.requestRenderAll(); } });
    bindInput("p-fontcolor", function (v) { var o = activeObj(); if (o && /text/i.test(o.type)) { o.set("fill", v); canvas.requestRenderAll(); } });
    var noFill = $("p-fill-none");
    if (noFill) noFill.addEventListener("change", function () { applyProp("fill", noFill.checked ? "" : ($("p-fill").value || "#c2ccd6")); });

    // Buton input bağları
    bindInput("bt-label", function (v) { btnSet(function (o, r, l) { if (l) l.set("text", v); }); });
    bindInput("bt-bg", function (v) { btnSet(function (o, r) { if (r) r.set("fill", v); }); });
    bindInput("bt-fg", function (v) { btnSet(function (o, r, l) { if (l) l.set("fill", v); }); });
    bindInput("bt-radius", function (v) { btnSet(function (o, r) { if (r) r.set({ rx: parseFloat(v) || 0, ry: parseFloat(v) || 0 }); }); });
    bindInput("bt-fontsize", function (v) { btnSet(function (o, r, l) { if (l) l.set("fontSize", parseFloat(v) || 14); }); });
    bindInput("bt-action", function (v) { var o = activeObj(); if (o && o.isButton) { o.scada = o.scada || {}; o.scada.action = v; markDirty(); } });
    bindInput("bt-setvalue", function (v) { var o = activeObj(); if (o && o.isButton) { o.scada = o.scada || {}; o.scada.setValue = parseFloat(v) || 0; markDirty(); } });

    // ----------------------------------------------------------------- //
    // Bağlama (animasyon) paneli
    // ----------------------------------------------------------------- //
    function syncBindings() {
        var o = activeObj(); if (!o) return;
        var sc = o.scada || (o.scada = { tag: "", anim: "none" });
        setVal("b-tag", sc.tag || "");
        if ($("b-anim")) $("b-anim").value = sc.anim || "none";
        setVal("b-min", sc.min == null ? 0 : sc.min);
        setVal("b-max", sc.max == null ? 100 : sc.max);
        setVal("b-threshold", sc.threshold == null ? 1 : sc.threshold);
        if ($("b-oncolor")) $("b-oncolor").value = sc.onColor || "#3fbf6f";
        if ($("b-offcolor")) $("b-offcolor").value = sc.offColor || "#e4544c";
        setVal("b-speed", sc.speed == null ? 1 : sc.speed);
        setVal("b-unit", sc.unit || "");
        setVal("b-decimals", sc.decimals == null ? 1 : sc.decimals);
        setVal("b-moverange", sc.moveRange == null ? 60 : sc.moveRange);
    }
    function bindScada(id, key, isNumber) {
        var el = $(id); if (!el) return;
        el.addEventListener("input", function () {
            var o = activeObj(); if (!o) return;
            o.scada = o.scada || {};
            o.scada[key] = isNumber ? (parseFloat(el.value) || 0) : el.value;
            markDirty();
        });
    }
    bindScada("b-tag", "tag");
    bindScada("b-anim", "anim");
    bindScada("b-min", "min", true);
    bindScada("b-max", "max", true);
    bindScada("b-threshold", "threshold", true);
    bindScada("b-oncolor", "onColor");
    bindScada("b-offcolor", "offColor");
    bindScada("b-speed", "speed", true);
    bindScada("b-unit", "unit");
    bindScada("b-decimals", "decimals", true);
    bindScada("b-moverange", "moveRange", true);

    // ----------------------------------------------------------------- //
    // Katmanlar
    // ----------------------------------------------------------------- //
    function refreshLayers() {
        var host = $("layer-list"); if (!host) return;
        host.innerHTML = "";
        var objs = canvas.getObjects().filter(function (o) { return !o.isHelper; });
        if ($("status-count")) $("status-count").textContent = objs.length + " obje";
        objs.slice().reverse().forEach(function (o) {
            var row = document.createElement("div");
            row.className = "layer-row" + (o === activeObj() ? " active" : "");
            var icon = o._objects ? "ki-element-11" : (/text/i.test(o.type) ? "ki-text" : "ki-abstract-26");
            row.innerHTML =
                '<i class="ki-duotone ' + icon + ' fs-6"><span class="path1"></span><span class="path2"></span></i>' +
                '<span class="layer-name">' + (o.name || o.type) + '</span>' +
                '<i class="lyr-act ki-duotone ' + (o.visible === false ? "ki-eye-slash" : "ki-eye") + ' fs-6" data-act="vis"><span class="path1"></span><span class="path2"></span><span class="path3"></span></i>' +
                '<i class="lyr-act ki-duotone ' + (o.selectable === false ? "ki-lock" : "ki-lock-3") + ' fs-6" data-act="lock"><span class="path1"></span><span class="path2"></span><span class="path3"></span></i>';
            row.addEventListener("click", function (e) {
                if (e.target.dataset.act) return;
                canvas.setActiveObject(o); canvas.requestRenderAll();
            });
            row.querySelector('[data-act="vis"]').addEventListener("click", function (e) {
                e.stopPropagation(); o.visible = o.visible === false; canvas.requestRenderAll(); refreshLayers();
            });
            row.querySelector('[data-act="lock"]').addEventListener("click", function (e) {
                e.stopPropagation();
                var lock = o.selectable !== false;
                o.selectable = !lock; o.evented = !lock; canvas.requestRenderAll(); refreshLayers();
            });
            host.appendChild(row);
        });
    }

    // ----------------------------------------------------------------- //
    // Düzen aksiyonları
    // ----------------------------------------------------------------- //
    var clipboard = null;
    function delActive() {
        var objs = canvas.getActiveObjects();
        objs.forEach(function (o) { if (!o.isHelper) canvas.remove(o); });
        canvas.discardActiveObject(); canvas.requestRenderAll(); refreshLayers();
    }
    function copyActive() { var o = activeObj(); if (o) o.clone(function (c) { clipboard = c; }, SER_PROPS); }
    function pasteActive() {
        if (!clipboard) return;
        clipboard.clone(function (c) {
            c.set({ left: c.left + 24, top: c.top + 24 });
            c.scada = JSON.parse(JSON.stringify(clipboard.scada || {}));
            canvas.add(c); canvas.setActiveObject(c); canvas.requestRenderAll(); refreshLayers();
        }, SER_PROPS);
    }
    function duplicateActive() { copyActive(); setTimeout(pasteActive, 30); }
    function groupActive() {
        var sel = canvas.getActiveObject();
        if (sel && sel.type === "activeSelection") { sel.toGroup(); canvas.requestRenderAll(); refreshLayers(); }
    }
    function ungroupActive() {
        var sel = canvas.getActiveObject();
        if (sel && sel.type === "group") { sel.toActiveSelection(); canvas.requestRenderAll(); refreshLayers(); }
    }
    function bringFront() { var o = activeObj(); if (o) { canvas.bringToFront(o); canvas.requestRenderAll(); refreshLayers(); } }
    function sendBack() { var o = activeObj(); if (o) { canvas.sendToBack(o); if (boundary) canvas.sendToBack(boundary); canvas.requestRenderAll(); refreshLayers(); } }
    function flipH() { var o = activeObj(); if (o) { o.set("flipX", !o.flipX); canvas.requestRenderAll(); pushUndo(); } }
    function flipV() { var o = activeObj(); if (o) { o.set("flipY", !o.flipY); canvas.requestRenderAll(); pushUndo(); } }

    // Çoklu seçim → seçim nesnelerini mutlak koordinatla geri al (selection çöz).
    function takeSelectionObjects() {
        var a = canvas.getActiveObject();
        if (a && a.type === "activeSelection") {
            var os = a.getObjects().slice();
            canvas.discardActiveObject();   // her objeye mutlak left/top geri yazılır
            return os;
        }
        return null;
    }
    function reselect(objs) {
        if (!objs || !objs.length) return;
        if (objs.length === 1) { canvas.setActiveObject(objs[0]); return; }
        var s = new fabric.ActiveSelection(objs, { canvas: canvas });
        canvas.setActiveObject(s);
    }
    function unionBounds(objs) {
        var l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
        objs.forEach(function (o) {
            var rc = o.getBoundingRect(true, true);
            l = Math.min(l, rc.left); t = Math.min(t, rc.top);
            r = Math.max(r, rc.left + rc.width); b = Math.max(b, rc.top + rc.height);
        });
        return { left: l, top: t, right: r, bottom: b };
    }
    // Bounding-rect kenarına göre kaydır (origin'den bağımsız).
    function moveEdge(o, how, target) {
        var rc = o.getBoundingRect(true, true);
        if (how === "left") o.left += target - rc.left;
        else if (how === "right") o.left += target - (rc.left + rc.width);
        else if (how === "centerH") o.left += target - (rc.left + rc.width / 2);
        else if (how === "top") o.top += target - rc.top;
        else if (how === "bottom") o.top += target - (rc.top + rc.height);
        else if (how === "middle") o.top += target - (rc.top + rc.height / 2);
        o.setCoords();
    }

    // Hizala: çoklu seçimde seçim sınırına, tek nesnede tuval (sayfa) sınırına göre.
    function align(how) {
        var multi = takeSelectionObjects();
        var objs = multi || (activeObj() ? [activeObj()] : []);
        if (!objs.length) return;
        var b = multi
            ? unionBounds(objs)
            : { left: 0, top: 0, right: state.width, bottom: state.height };
        var targets = {
            left: b.left, right: b.right, centerH: (b.left + b.right) / 2,
            top: b.top, bottom: b.bottom, middle: (b.top + b.bottom) / 2
        };
        if (targets[how] == null) return;
        objs.forEach(function (o) { moveEdge(o, how, targets[how]); });
        reselect(multi ? objs : null);
        canvas.requestRenderAll(); pushUndo();
    }

    // Dağıt: seçili nesneleri (3+) yatay/dikey eşit aralıklarla yay.
    function distribute(axis) {
        var objs = takeSelectionObjects();
        if (!objs || objs.length < 3) {
            if (objs) reselect(objs);
            toast("Dağıtmak için en az 3 nesne seçin.", "danger");
            return;
        }
        var key = axis === "v" ? "y" : "x";
        objs.sort(function (a, b) { return a.getCenterPoint()[key] - b.getCenterPoint()[key]; });
        var first = objs[0].getCenterPoint()[key];
        var last = objs[objs.length - 1].getCenterPoint()[key];
        var step = (last - first) / (objs.length - 1);
        objs.forEach(function (o, i) {
            var c = o.getCenterPoint();
            var nx = axis === "v" ? c.x : first + step * i;
            var ny = axis === "v" ? first + step * i : c.y;
            o.setPositionByOrigin(new fabric.Point(nx, ny), "center", "center");
            o.setCoords();
        });
        reselect(objs);
        canvas.requestRenderAll(); pushUndo();
    }

    // ----------------------------------------------------------------- //
    // Kaydet / yükle / dışa aktar
    // ----------------------------------------------------------------- //
    // Dışa aktarımda viewport transform'u geçici sıfırla — yoksa zoom/pan
    // nedeniyle yakalanan bölge tasarım koordinatlarıyla hizalanmaz.
    function withIdentityVpt(fn) {
        var vpt = canvas.viewportTransform.slice();
        canvas.setViewportTransform([1, 0, 0, 1, 0, 0]);
        var out;
        try { out = fn(); } finally { canvas.setViewportTransform(vpt); }
        return out;
    }
    function makeThumbnail() {
        try {
            var mult = Math.min(360 / state.width, 1);
            return withIdentityVpt(function () {
                return canvas.toDataURL({
                    format: "png", multiplier: mult,
                    left: 0, top: 0, width: state.width, height: state.height
                });
            });
        } catch (e) { return ""; }
    }
    function payload() {
        return {
            id: state.screenId,
            name: state.name || $("mimic-name").value || "Adsız Mimik",
            description: ($("mimic-desc") ? $("mimic-desc").value : "") || "",
            width: state.width, height: state.height, background: state.background,
            data: canvas.toJSON(SER_PROPS),
            thumbnail: makeThumbnail()
        };
    }
    function save(asNew) {
        state.name = $("mimic-name").value.trim();
        if (!state.name) { toast("Lütfen bir ekran adı girin.", "danger"); $("mimic-name").focus(); return; }
        var p = payload();
        if (asNew) p.id = null;
        fetch(CFG.urls.save, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": CFG.csrf },
            body: JSON.stringify(p)
        }).then(function (r) { return r.json(); }).then(function (d) {
            if (!d.ok) { toast(d.error || "Kayıt hatası", "danger"); return; }
            state.screenId = d.id;
            clearDirty();
            toast("Mimik kaydedildi.", "success");
            // URL'i editör-edit moduna güncelle (yenilemeden)
            if (history.replaceState) history.replaceState(null, "", CFG.urls.editorBase + d.id + "/");
        }).catch(function () { toast("Sunucuya ulaşılamadı.", "danger"); });
    }

    function loadData(data) {
        suspend = true;
        canvas.loadFromJSON(data, function () {
            rebuildBoundary();
            applyBackground();
            canvas.requestRenderAll();
            suspend = false;
            undoStack = [serialize()]; redoStack = [];
            refreshLayers();
            zoomFit();
        });
    }

    function exportPNG() {
        var url = withIdentityVpt(function () {
            return canvas.toDataURL({ format: "png", multiplier: 2, left: 0, top: 0, width: state.width, height: state.height });
        });
        download(url, (state.name || "mimik") + ".png");
    }
    function exportSVG() {
        var svg = canvas.toSVG({ viewBox: { x: 0, y: 0, width: state.width, height: state.height }, width: state.width, height: state.height });
        download("data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg), (state.name || "mimik") + ".svg");
    }
    function exportJSON() {
        var blob = new Blob([JSON.stringify(payload(), null, 2)], { type: "application/json" });
        download(URL.createObjectURL(blob), (state.name || "mimik") + ".mimic.json");
    }
    function importJSON(file) {
        var r = new FileReader();
        r.onload = function (e) {
            try {
                var obj = JSON.parse(e.target.result);
                var data = obj.data || obj;
                if (obj.name) { state.name = obj.name; $("mimic-name").value = obj.name; }
                if (obj.width) state.width = obj.width;
                if (obj.height) state.height = obj.height;
                if (obj.background) state.background = obj.background;
                loadData(data);
                toast("Tasarım içe aktarıldı.", "success");
            } catch (err) { toast("Geçersiz dosya.", "danger"); }
        };
        r.readAsText(file);
    }
    function download(url, name) {
        var a = document.createElement("a");
        a.href = url; a.download = name; a.click();
        if (url.indexOf("blob:") === 0) setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    }

    // ----------------------------------------------------------------- //
    // Simülasyon modu
    // ----------------------------------------------------------------- //
    var simInterval = null, simLiveTimer = null;
    function buildSimPanel() {
        var host = $("sim-tags"); if (!host) return;
        host.innerHTML = "";
        var tags = runtime.tagList();
        if (!tags.length) {
            host.innerHTML = '<div class="text-muted fs-8 p-3">Animasyon bağlı obje yok. Bir objeye etiket + animasyon atayın.</div>';
            return;
        }
        tags.forEach(function (t) {
            var row = document.createElement("div");
            row.className = "sim-row";
            row.innerHTML =
                '<label>' + t + '</label>' +
                '<input type="range" min="0" max="100" value="0" data-tag="' + t + '">' +
                '<span class="sim-val" data-val="' + t + '">0</span>' +
                '<button class="sim-toggle" data-toggle="' + t + '">0/1</button>';
            var rng = row.querySelector("input");
            rng.addEventListener("input", function () {
                runtime.setTag(t, rng.value);
                row.querySelector('[data-val]').textContent = rng.value;
            });
            row.querySelector("[data-toggle]").addEventListener("click", function () {
                var nv = (Number(rng.value) > 0) ? 0 : 100;
                rng.value = nv; runtime.setTag(t, nv);
                row.querySelector('[data-val]').textContent = nv;
            });
            host.appendChild(row);
        });
    }
    function startSim() {
        document.body.classList.add("sim-mode");
        canvas.discardActiveObject();
        // Diğer objeler kilitli; butonlar tıklanabilir kalır (etiket aksiyonu).
        canvas.forEachObject(function (o) {
            o.selectable = false;
            o.evented = !!o.isButton;
            if (o.isButton) o.hoverCursor = "pointer";
        });
        canvas.requestRenderAll();
        runtime.start();
        buildSimPanel();
        var auto = $("sim-auto");
        if (auto && auto.checked) startAuto();
        var live = $("sim-live");
        if (live && live.checked) startLive();
    }
    function stopSim() {
        document.body.classList.remove("sim-mode");
        runtime.stop();
        stopAuto();
        stopLive();
        canvas.forEachObject(function (o) { if (!o.isHelper) { o.selectable = true; o.evented = true; } });
        canvas.requestRenderAll();
    }
    // Canlı mod: gerçek sensör değerlerini periyodik çek → runtime'a uygula.
    function pollLive() {
        fetch(CFG.urls.tags, { headers: { "X-Requested-With": "XMLHttpRequest" } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                if (!d || !d.values) return;
                Object.keys(d.values).forEach(function (t) {
                    runtime.setTag(t, d.values[t]);
                    refreshSimRow(t);
                });
            }).catch(function () {});
    }
    function startLive() {
        stopAuto();                       // canlı varken rastgele kapalı
        var a = $("sim-auto"); if (a) a.checked = false;
        stopLive();
        pollLive();
        simLiveTimer = setInterval(pollLive, 4000);
    }
    function stopLive() { if (simLiveTimer) { clearInterval(simLiveTimer); simLiveTimer = null; } }
    function startAuto() {
        stopAuto();
        simInterval = setInterval(function () {
            runtime.tagList().forEach(function (t) {
                var v = Math.round(Math.random() * 100);
                runtime.setTag(t, v);
                var disp = document.querySelector('[data-val="' + t + '"]');
                var rng = document.querySelector('input[data-tag="' + t + '"]');
                if (disp) disp.textContent = v;
                if (rng) rng.value = v;
            });
        }, 1500);
    }
    function stopAuto() { if (simInterval) { clearInterval(simInterval); simInterval = null; } }

    function togglePreview() {
        if (runtime.isRunning()) { stopSim(); setPreviewBtn(false); }
        else { startSim(); setPreviewBtn(true); }
    }
    function setPreviewBtn(on) {
        var b = $("btn-preview"); if (!b) return;
        b.classList.toggle("btn-danger", on);
        b.classList.toggle("btn-success", !on);
        b.innerHTML = on
            ? '<i class="ki-duotone ki-cross-square fs-3"></i> Simülasyonu Durdur'
            : '<i class="ki-duotone ki-play fs-3"><span class="path1"></span><span class="path2"></span></i> Simülasyon';
    }

    // ----------------------------------------------------------------- //
    // Toast
    // ----------------------------------------------------------------- //
    function toast(msg, type) {
        var t = document.createElement("div");
        t.className = "mimic-toast " + (type || "info");
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(function () { t.classList.add("show"); }, 10);
        setTimeout(function () { t.classList.remove("show"); setTimeout(function () { t.remove(); }, 300); }, 2600);
    }

    // ----------------------------------------------------------------- //
    // Aç diyaloğu (kayıtlı mimikler)
    // ----------------------------------------------------------------- //
    function openDialog() {
        fetch(CFG.urls.list, { headers: { "X-Requested-With": "XMLHttpRequest" } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                var host = $("open-list"); host.innerHTML = "";
                (d.results || []).forEach(function (m) {
                    var card = document.createElement("div");
                    card.className = "open-card";
                    card.innerHTML =
                        '<div class="open-thumb">' + (m.thumbnail ? '<img src="' + m.thumbnail + '">' : '<span>—</span>') + '</div>' +
                        '<div class="open-meta"><b>' + m.name + '</b><small>' + m.updated_at + '</small></div>';
                    card.addEventListener("click", function () {
                        window.location.href = CFG.urls.editorBase + m.id + "/";
                    });
                    host.appendChild(card);
                });
                if (!d.results || !d.results.length) host.innerHTML = '<div class="text-muted p-4">Kayıtlı mimik yok.</div>';
                showModal("open-modal");
            });
    }
    function showModal(id) { $(id).classList.add("show"); }
    function hideModal(id) { $(id).classList.remove("show"); }

    // ----------------------------------------------------------------- //
    // Olaylar / wiring
    // ----------------------------------------------------------------- //
    canvas.on("selection:created", function () { syncProps(); refreshLayers(); });
    canvas.on("selection:updated", function () { syncProps(); refreshLayers(); });
    canvas.on("selection:cleared", function () { syncProps(); refreshLayers(); });
    canvas.on("object:modified", syncProps);
    canvas.on("object:scaling", syncProps);
    canvas.on("object:moving", syncProps);
    canvas.on("object:rotating", syncProps);

    function on(id, ev, fn) { var el = $(id); if (el) el.addEventListener(ev, fn); }
    // Şekil butonları
    document.querySelectorAll("[data-shape]").forEach(function (b) {
        b.addEventListener("click", function () { addShape(b.dataset.shape); });
    });
    on("symbol-search", "input", function (e) { buildPalette(e.target.value); });
    on("btn-add-image", "click", function () { $("file-image").click(); });
    on("file-image", "change", function (e) { if (e.target.files[0]) addImageFile(e.target.files[0]); e.target.value = ""; });

    on("btn-save", "click", function () { save(false); });
    on("btn-saveas", "click", function () { save(true); });
    on("btn-new", "click", function () { if (!state.dirty || confirm("Kaydedilmemiş değişiklikler kaybolacak. Devam?")) window.location.href = CFG.urls.editorNew; });
    on("btn-open", "click", openDialog);
    on("open-close", "click", function () { hideModal("open-modal"); });
    on("btn-export-png", "click", exportPNG);
    on("btn-export-svg", "click", exportSVG);
    on("btn-export-json", "click", exportJSON);
    on("btn-import-json", "click", function () { $("file-import").click(); });
    on("file-import", "change", function (e) { if (e.target.files[0]) importJSON(e.target.files[0]); e.target.value = ""; });
    on("btn-preview", "click", togglePreview);
    on("btn-view-tab", "click", function () { if (state.screenId) window.open(CFG.urls.viewerBase + state.screenId + "/", "_blank"); else toast("Önce kaydedin.", "danger"); });

    on("btn-undo", "click", undo);
    on("btn-redo", "click", redo);
    on("btn-delete", "click", delActive);
    on("btn-duplicate", "click", duplicateActive);
    on("btn-copy", "click", copyActive);
    on("btn-paste", "click", pasteActive);
    on("btn-front", "click", bringFront);
    on("btn-back", "click", sendBack);
    on("btn-group", "click", groupActive);
    on("btn-ungroup", "click", ungroupActive);
    on("btn-fliph", "click", flipH);
    on("btn-flipv", "click", flipV);
    document.querySelectorAll("[data-align]").forEach(function (b) {
        b.addEventListener("click", function () { align(b.dataset.align); });
    });
    document.querySelectorAll("[data-distribute]").forEach(function (b) {
        b.addEventListener("click", function () { distribute(b.dataset.distribute); });
    });

    // Açılır menüler (Dosya / Aktar)
    function closeMenus() {
        document.querySelectorAll(".r-menu.open").forEach(function (m) { m.classList.remove("open"); });
    }
    document.querySelectorAll("[data-menu-toggle]").forEach(function (t) {
        t.addEventListener("click", function (e) {
            e.stopPropagation();
            var menu = t.closest(".r-menu");
            var isOpen = menu.classList.contains("open");
            closeMenus();
            if (!isOpen) menu.classList.add("open");
        });
    });
    document.querySelectorAll(".r-menu-list .menu-item").forEach(function (it) {
        it.addEventListener("click", function () { setTimeout(closeMenus, 0); });
    });
    document.addEventListener("click", closeMenus);

    on("zoom-in", "click", function () { setZoom(canvas.getZoom() * 1.15); });
    on("zoom-out", "click", function () { setZoom(canvas.getZoom() / 1.15); });
    on("zoom-100", "click", function () { canvas.setViewportTransform([1, 0, 0, 1, canvas.viewportTransform[4], canvas.viewportTransform[5]]); setZoom(1); });
    on("zoom-fit", "click", zoomFit);

    on("chk-grid", "change", function (e) { state.grid = e.target.checked; applyBackground(); });
    on("chk-snap", "change", function (e) { state.snap = e.target.checked; });

    // Tuval boyutu / arka plan
    on("cfg-width", "change", function (e) { state.width = parseInt(e.target.value) || 1280; rebuildBoundary(); zoomFit(); });
    on("cfg-height", "change", function (e) { state.height = parseInt(e.target.value) || 720; rebuildBoundary(); zoomFit(); });
    on("cfg-bg", "input", function (e) { state.background = e.target.value; applyBackground(); });

    // Sağ panel sekmeleri
    document.querySelectorAll(".rp-tab").forEach(function (t) {
        t.addEventListener("click", function () {
            document.querySelectorAll(".rp-tab").forEach(function (x) { x.classList.remove("active"); });
            document.querySelectorAll(".rp-pane").forEach(function (x) { x.classList.remove("active"); });
            t.classList.add("active");
            $(t.dataset.pane).classList.add("active");
        });
    });

    // Sol panel sekmeleri (Semboller / Şekiller)
    document.querySelectorAll(".lp-tab").forEach(function (t) {
        t.addEventListener("click", function () {
            document.querySelectorAll(".lp-tab").forEach(function (x) { x.classList.remove("active"); });
            document.querySelectorAll(".lp-pane").forEach(function (x) { x.classList.remove("active"); });
            t.classList.add("active");
            $(t.dataset.pane).classList.add("active");
        });
    });

    on("sim-auto", "change", function (e) {
        if (e.target.checked) { var l = $("sim-live"); if (l) l.checked = false; stopLive(); }
        if (runtime.isRunning()) { e.target.checked ? startAuto() : stopAuto(); }
    });
    on("sim-live", "change", function (e) {
        if (e.target.checked) { var a = $("sim-auto"); if (a) a.checked = false; stopAuto(); }
        if (runtime.isRunning()) { e.target.checked ? startLive() : stopLive(); }
    });

    // Klavye kısayolları
    document.addEventListener("keydown", function (e) {
        if (e.code === "Space" && !isTyping(e)) { spaceDown = true; }
        if (isTyping(e)) return;
        var meta = e.ctrlKey || e.metaKey;
        if (meta && e.key.toLowerCase() === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); }
        else if (meta && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); }
        else if (meta && e.key.toLowerCase() === "s") { e.preventDefault(); save(false); }
        else if (meta && e.key.toLowerCase() === "c") { copyActive(); }
        else if (meta && e.key.toLowerCase() === "v") { pasteActive(); }
        else if (meta && e.key.toLowerCase() === "d") { e.preventDefault(); duplicateActive(); }
        else if (meta && e.key.toLowerCase() === "g") { e.preventDefault(); e.shiftKey ? ungroupActive() : groupActive(); }
        else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); delActive(); }
        else if (e.key === "Escape") { canvas.discardActiveObject(); canvas.requestRenderAll(); }
        else if (e.key.startsWith("Arrow")) {
            var o = activeObj(); if (o) {
                var d = e.shiftKey ? 10 : 1;
                if (e.key === "ArrowLeft") o.left -= d;
                if (e.key === "ArrowRight") o.left += d;
                if (e.key === "ArrowUp") o.top -= d;
                if (e.key === "ArrowDown") o.top += d;
                o.setCoords(); canvas.requestRenderAll(); e.preventDefault();
            }
        }
    });
    document.addEventListener("keyup", function (e) { if (e.code === "Space") spaceDown = false; });
    function isTyping(e) {
        var t = e.target.tagName;
        return t === "INPUT" || t === "TEXTAREA" || (canvas.getActiveObject() && canvas.getActiveObject().isEditing);
    }

    window.addEventListener("beforeunload", function (e) {
        if (state.dirty) { e.preventDefault(); e.returnValue = ""; }
    });
    window.addEventListener("resize", function () {
        resizeCanvas();
    });

    function resizeCanvas() {
        var wrap = $("canvas-wrap");
        canvas.setWidth(wrap.clientWidth);
        canvas.setHeight(wrap.clientHeight);
        canvas.requestRenderAll();
    }

    // ----------------------------------------------------------------- //
    // Gerçek SCADA etiketleri (sensör tag'leri) — datalist + canlı değer
    // ----------------------------------------------------------------- //
    var TAGS = [], TAGMAP = {};
    function loadTags() {
        if (!CFG.urls || !CFG.urls.tags) return;
        fetch(CFG.urls.tags, { headers: { "X-Requested-With": "XMLHttpRequest" } })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                TAGS = (d && d.results) || [];
                TAGMAP = {};
                var dl = $("tag-options");
                if (dl) dl.innerHTML = "";
                TAGS.forEach(function (it) {
                    TAGMAP[it.tag] = it;
                    if (dl) {
                        var opt = document.createElement("option");
                        opt.value = it.tag;
                        opt.label = it.label + (it.station ? " · " + it.station : "") +
                                    (it.unit ? " (" + it.unit + ")" : "");
                        dl.appendChild(opt);
                    }
                });
            }).catch(function () {});
    }
    function tagInfo(tag) {
        var it = TAGMAP[tag];
        if (!it) return "";
        return it.label + (it.unit ? " · " + it.unit : "") + " — son değer: " + it.value;
    }

    // ----------------------------------------------------------------- //
    // Sağ-tık menüsü — hızlı tag + animasyon ataması
    // ----------------------------------------------------------------- //
    var ctxObj = null;
    function showCtxMenu(o, x, y) {
        ctxObj = o;
        var sc = o.scada || (o.scada = { tag: "", anim: "none" });
        if ($("ctx-objname")) $("ctx-objname").textContent = (o.name || o.type) + (o.symbolKey ? " · " + o.symbolKey : "");
        if ($("ctx-tag")) $("ctx-tag").value = sc.tag || "";
        if ($("ctx-anim")) $("ctx-anim").value = sc.anim || "none";
        updateCtxLive();
        var m = $("ctx-menu"); m.classList.add("show");
        // ekran içinde tut
        var mw = m.offsetWidth || 252, mh = m.offsetHeight || 300;
        m.style.left = Math.min(x, window.innerWidth - mw - 8) + "px";
        m.style.top = Math.min(y, window.innerHeight - mh - 8) + "px";
    }
    function hideCtxMenu() { var m = $("ctx-menu"); if (m) m.classList.remove("show"); }
    function updateCtxLive() {
        var el = $("ctx-livehint"); if (!el) return;
        var tag = $("ctx-tag") ? $("ctx-tag").value.trim() : "";
        el.innerHTML = (tag && TAGMAP[tag]) ? ("<b>" + tag + "</b> → " + tagInfo(tag)) :
            (tag ? "Serbest etiket (sensör listesinde yok)" : "");
    }
    // sağ tık → menü
    canvas.on("mouse:down", function (opt) {
        if (runtime.isRunning()) return;
        if (opt.e.button === 2 && opt.target && !opt.target.isHelper) {
            canvas.setActiveObject(opt.target);
            canvas.requestRenderAll();
            showCtxMenu(opt.target, opt.e.clientX, opt.e.clientY);
        } else {
            hideCtxMenu();
        }
    });
    document.addEventListener("mousedown", function (e) {
        var m = $("ctx-menu");
        if (m && m.classList.contains("show") && !m.contains(e.target)) {
            // canvas üstündeki sağ tık zaten yukarıda ele alınıyor
            if (e.button !== 2) hideCtxMenu();
        }
    });
    on("ctx-tag", "input", function () {
        if (ctxObj) { ctxObj.scada = ctxObj.scada || {}; ctxObj.scada.tag = $("ctx-tag").value; markDirty(); syncBindings(); }
        updateCtxLive();
    });
    on("ctx-anim", "change", function () {
        if (ctxObj) { ctxObj.scada = ctxObj.scada || {}; ctxObj.scada.anim = $("ctx-anim").value; markDirty(); syncBindings(); }
    });
    on("ctx-detail", "click", function () {
        hideCtxMenu();
        var t = document.querySelector('.rp-tab[data-pane="rp-bind"]');
        if (t) t.click();
    });
    on("ctx-clear", "click", function () {
        if (ctxObj) { ctxObj.scada = { tag: "", anim: "none" }; markDirty(); syncBindings(); }
        if ($("ctx-tag")) $("ctx-tag").value = "";
        if ($("ctx-anim")) $("ctx-anim").value = "none";
        updateCtxLive();
    });
    on("ctx-dup", "click", function () { hideCtxMenu(); duplicateActive(); });
    on("ctx-front", "click", function () { hideCtxMenu(); bringFront(); });
    on("ctx-back", "click", function () { hideCtxMenu(); sendBack(); });
    on("ctx-del", "click", function () { hideCtxMenu(); delActive(); });

    // ----------------------------------------------------------------- //
    // Başlat
    // ----------------------------------------------------------------- //
    function init() {
        resizeCanvas();
        buildPalette("");
        loadTags();
        if ($("mimic-name")) $("mimic-name").value = state.name;
        if ($("cfg-width")) $("cfg-width").value = state.width;
        if ($("cfg-height")) $("cfg-height").value = state.height;
        if ($("cfg-bg")) $("cfg-bg").value = state.background;
        if ($("mimic-desc") && CFG.initial && CFG.initial.description) $("mimic-desc").value = CFG.initial.description;
        rebuildBoundary();
        applyBackground();
        setPreviewBtn(false);

        if (CFG.initial && CFG.initial.data && Object.keys(CFG.initial.data).length) {
            loadData(CFG.initial.data);
        } else {
            undoStack = [serialize()];
            zoomFit();
        }
        refreshLayers();
        syncProps();
    }

    if (document.readyState === "complete" || document.readyState === "interactive") {
        setTimeout(init, 30);
    } else {
        document.addEventListener("DOMContentLoaded", init);
    }
})();
