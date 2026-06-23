/* ===========================================================================
 * mimic_runtime.js — Mimik animasyon / simülasyon motoru
 * ---------------------------------------------------------------------------
 * Editör "Simülasyon" modu ve standalone görüntüleyici tarafından paylaşılır.
 * Bir fabric.Canvas + etiket(tag) değer haritası alır; her objenin `scada`
 * bağlama meta verisine göre animasyonları (renk/durum, yanıp sönme, dönme,
 * doluluk, görünürlük, metin, hareket, saydamlık) sürer.
 *
 * Bağlama şeması (obj.scada):
 *   { tag, anim, min, max, onColor, offColor, threshold, speed,
 *     unit, decimals, moveRange }
 * ======================================================================== */
(function () {
    "use strict";

    function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
    function ratioOf(val, mn, mx) {
        if (mx === mn) return 0;
        return clamp((val - mn) / (mx - mn), 0, 1);
    }
    function num(v, d) { v = Number(v); return isNaN(v) ? d : v; }
    function frac(x) { return x - Math.floor(x); }
    function nowMs() { return (window.performance && performance.now) ? performance.now() : Date.now(); }

    // --------------------------------------------------------------------- //
    // Sembole özel ("auto") animasyon sınıflandırması — symbolKey'e göre.
    //   water   : tank/havuz → seviye + dalga overlay
    //   aeration: su + yükselen kabarcıklar
    //   spin    : pompa/motor/fan → merkezde dönen rotor overlay
    //   gauge   : gösterge ibresi
    //   flow    : boru → akış çizgileri
    //   tint    : vana/lamba/pano → durum rengi (gövde boyanır)
    //   blink   : alarm/çakar → yanıp sönme
    // --------------------------------------------------------------------- //
    var AUTO_OVERRIDE = {
        gauge: "gauge", rotameter: "gauge", transmitter: "tint", analyzer: "tint",
        aeration: "aeration", flow_arrow: "flow", screw_conveyor: "flow",
        beacon: "blink", alarm_horn: "blink", emergency_stop: "blink",
        gas_detector: "blink", lamp: "tint", value_display: "tint",
        flow_cell: "water", sample_cell: "water"
    };
    function autoKind(o) {
        var k = o.symbolKey || "";
        if (AUTO_OVERRIDE[k]) return AUTO_OVERRIDE[k];
        if (/^(tank|silo|reactor|basin|clarifier|wet_well|grit|weir|open_channel|dosing_tank|sand_filter|carbon_filter|bag_filter|cartridge)/.test(k)) return "water";
        if (/^(pump|motor|fan|mixer|blower|compressor|dosing_pump|uf_module|ro_membrane)/.test(k)) return "spin";
        if (/^(pipe|union|flange|expansion|strainer)/.test(k)) return "flow";
        if (/^(valve|solenoid|lamp|pushbutton|switch|ups|plc|cabinet|hmi|rtu|breaker|vfd|generator|solar|energy|level_switch|float)/.test(k)) return "tint";
        return "tint";
    }
    var OVERLAY_KINDS = { water: 1, aeration: 1, spin: 1, gauge: 1, flow: 1 };

    // --------------------------------------------------------------------- //
    // Overlay çizimleri (sahne koordinatında; viewport transform uygulanmış halde)
    // --------------------------------------------------------------------- //
    function drawWater(ctx, r, ratio, t, bubbles) {
        var inset = 0.12;
        var x = r.left + r.width * inset, w = r.width * (1 - 2 * inset);
        var top = r.top + r.height * 0.10, fullH = r.height * 0.82;
        var h = fullH * ratio, y = top + fullH - h;
        var amp = clamp(w * 0.035, 1.5, 5), k = (2 * Math.PI) / (w / 1.25), ph = t / 280;
        ctx.beginPath();
        ctx.moveTo(x, y);
        for (var px = 0; px <= w; px += 3) ctx.lineTo(x + px, y + Math.sin(px * k + ph) * amp);
        ctx.lineTo(x + w, top + fullH); ctx.lineTo(x, top + fullH); ctx.closePath();
        ctx.fillStyle = "rgba(47,155,214,0.55)"; ctx.fill();
        ctx.strokeStyle = "rgba(150,210,240,0.85)"; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(x, y);
        for (px = 0; px <= w; px += 3) ctx.lineTo(x + px, y + Math.sin(px * k + ph) * amp);
        ctx.stroke();
        if (bubbles && h > 4) {
            ctx.fillStyle = "rgba(255,255,255,0.7)";
            for (var i = 0; i < 9; i++) {
                var seed = i * 1.61;
                var bx = x + w * (0.08 + 0.84 * frac(seed * 0.37));
                var prog = frac(t / 1500 + i * 0.19);
                var by = top + fullH - prog * (h - 2);
                var rad = 1.4 + 1.6 * frac(seed);
                ctx.beginPath(); ctx.arc(bx, by, rad, 0, 2 * Math.PI); ctx.fill();
            }
        }
    }
    function drawSpin(ctx, r, t, speed) {
        if (speed <= 0) return;
        var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
        var rad = Math.min(r.width, r.height) * 0.27;
        var ang = (t / 1000) * speed * 3.2;
        ctx.save();
        ctx.translate(cx, cy); ctx.rotate(ang);
        ctx.strokeStyle = "rgba(63,191,111,0.95)";
        ctx.lineWidth = Math.max(2, rad * 0.16); ctx.lineCap = "round";
        for (var s = 0; s < 3; s++) {
            ctx.rotate((2 * Math.PI) / 3);
            ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(rad, 0); ctx.stroke();
        }
        ctx.beginPath(); ctx.arc(0, 0, rad * 0.2, 0, 2 * Math.PI);
        ctx.fillStyle = "rgba(63,191,111,1)"; ctx.fill();
        ctx.restore();
    }
    function drawGauge(ctx, r, ratio) {
        var cx = r.left + r.width / 2, cy = r.top + r.height * 0.52;
        var rad = Math.min(r.width, r.height) * 0.36;
        var a = (210 + ratio * 120) * Math.PI / 180;
        ctx.strokeStyle = "#e4544c"; ctx.lineWidth = Math.max(2, rad * 0.12); ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(cx, cy);
        ctx.lineTo(cx + Math.cos(a) * rad, cy + Math.sin(a) * rad); ctx.stroke();
        ctx.beginPath(); ctx.arc(cx, cy, rad * 0.14, 0, 2 * Math.PI);
        ctx.fillStyle = "#37414d"; ctx.fill();
    }
    function drawFlow(ctx, r, t, active) {
        if (!active) return;
        var horiz = r.width >= r.height;
        ctx.strokeStyle = "rgba(47,155,214,0.95)";
        ctx.lineCap = "round";
        ctx.setLineDash([5, 11]);
        ctx.lineDashOffset = -(t / 38) % 32;
        if (horiz) {
            ctx.lineWidth = Math.max(3, r.height * 0.32);
            var cy = r.top + r.height / 2;
            ctx.beginPath(); ctx.moveTo(r.left + 4, cy); ctx.lineTo(r.left + r.width - 4, cy); ctx.stroke();
        } else {
            ctx.lineWidth = Math.max(3, r.width * 0.32);
            var cx = r.left + r.width / 2;
            ctx.beginPath(); ctx.moveTo(cx, r.top + 4); ctx.lineTo(cx, r.top + r.height - 4); ctx.stroke();
        }
        ctx.setLineDash([]);
    }

    // Boru merkez hatları (normalize 0..1, sembolün viewBox'ına göre). Akış,
    // objenin KENDİ dönüşüm matrisiyle çizilir → dirsek köşeyi döner, tee/cross
    // dallanır, döndürme/aynalama otomatik takip edilir.
    var FLOW_PATHS = {
        pipe_h: [[[0, 0.5], [1, 0.5]]],
        pipe_v: [[[0.5, 0], [0.5, 1]]],
        // dirsek: üst açıklıktan sol açıklığa çeyrek yay (içerik kutusuna göre)
        pipe_elbow: [[[0.857, 0], [0.743, 0.429], [0.429, 0.743], [0, 0.857]]],
        pipe_tee: [[[0, 0.12], [1, 0.12]], [[0.5, 0.12], [0.5, 1]]],
        pipe_cross: [[[0, 0.5], [1, 0.5]], [[0.5, 0], [0.5, 1]]],
        pipe_reducer: [[[0, 0.5], [1, 0.5]]],
        flow_arrow: [[[0, 0.5], [1, 0.5]]]
    };
    function drawFlowPath(ctx, obj, paths, t) {
        var m = obj.calcTransformMatrix();
        var w = obj.width, h = obj.height;
        function P(n) {
            return fabric.util.transformPoint(
                new fabric.Point((n[0] - 0.5) * w, (n[1] - 0.5) * h), m);
        }
        var lw = Math.max(3, Math.min(obj.getScaledWidth(), obj.getScaledHeight()) * 0.34);
        ctx.strokeStyle = "rgba(47,155,214,0.95)";
        ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.lineWidth = lw;
        ctx.setLineDash([5, 11]); ctx.lineDashOffset = -(t / 38) % 32;
        paths.forEach(function (poly) {
            ctx.beginPath();
            poly.forEach(function (n, i) {
                var p = P(n);
                if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y);
            });
            ctx.stroke();
        });
        ctx.setLineDash([]);
    }

    // Bir objenin (grup ise tüm yapraklarının) dolgulu parçalarına renk uygula.
    function eachLeaf(obj, cb) {
        if (obj._objects && obj._objects.length) {
            obj._objects.forEach(function (o) { eachLeaf(o, cb); });
        } else {
            cb(obj);
        }
    }
    function isFillable(leaf) {
        var f = leaf.fill;
        return f && f !== "none" && f !== "transparent" &&
               !(typeof f === "object");
    }

    function MimicRuntime(canvas) {
        var raf = null, running = false, lastT = 0;
        var snaps = new Map();       // obj -> snapshot
        var tags = {};               // tag -> number

        function bound() {
            return canvas.getObjects().filter(function (o) {
                return o.scada && o.scada.anim && o.scada.anim !== "none";
            });
        }

        function snapshot(obj) {
            var leafFills = [];
            eachLeaf(obj, function (l) { leafFills.push([l, l.fill]); });
            snaps.set(obj, {
                angle: obj.angle || 0,
                top: obj.top, left: obj.left,
                scaleY: obj.scaleY || 1,
                opacity: obj.opacity == null ? 1 : obj.opacity,
                visible: obj.visible !== false,
                text: obj.text,
                scaledH: obj.getScaledHeight ? obj.getScaledHeight() : (obj.height || 0),
                leafFills: leafFills,
                rotAccum: 0
            });
        }

        function applyColor(obj, color) {
            eachLeaf(obj, function (l) { if (isFillable(l)) l.set("fill", color); });
        }

        function restore(obj) {
            var s = snaps.get(obj);
            if (!s) return;
            obj.set({ angle: s.angle, top: s.top, left: s.left,
                      scaleY: s.scaleY, opacity: s.opacity, visible: s.visible });
            if (s.text != null && obj.set) { try { obj.set("text", s.text); } catch (e) {} }
            s.leafFills.forEach(function (pair) { pair[0].set("fill", pair[1]); });
        }

        function tick(now) {
            if (!running) return;
            var dt = lastT ? (now - lastT) / 1000 : 0;
            lastT = now;

            bound().forEach(function (obj) {
                var sc = obj.scada, s = snaps.get(obj);
                if (!s) { snapshot(obj); s = snaps.get(obj); }
                var val = Number(tags[sc.tag]);
                if (isNaN(val)) val = 0;
                var thr = sc.threshold == null ? 1 : Number(sc.threshold);
                var mn = sc.min == null ? 0 : Number(sc.min);
                var mx = sc.max == null ? 100 : Number(sc.max);

                switch (sc.anim) {
                    case "colorState":
                    case "fillThreshold":
                        applyColor(obj, (val >= thr) ? (sc.onColor || "#3fbf6f")
                                                     : (sc.offColor || "#e4544c"));
                        break;
                    case "blink":
                        if (val >= thr) {
                            obj.set("opacity", 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(now / 130)));
                        } else {
                            obj.set("opacity", s.opacity);
                        }
                        break;
                    case "rotate":
                        if (val > thr || (val > 0 && thr <= 0)) {
                            s.rotAccum += dt * 90 * (sc.speed || 1);
                            obj.set("angle", s.angle + s.rotAccum);
                        }
                        break;
                    case "level": {
                        var r = ratioOf(val, mn, mx);
                        var bottom = s.top + s.scaledH;
                        var targetH = s.scaledH * r;
                        obj.set({ scaleY: s.scaleY * r, top: bottom - targetH });
                        break;
                    }
                    case "visibility":
                        obj.set("visible", val >= thr);
                        break;
                    case "opacity":
                        obj.set("opacity", ratioOf(val, mn, mx));
                        break;
                    case "moveX":
                        obj.set("left", s.left + ratioOf(val, mn, mx) * (sc.moveRange || 100));
                        break;
                    case "moveY":
                        obj.set("top", s.top + ratioOf(val, mn, mx) * (sc.moveRange || 100));
                        break;
                    case "text":
                        if (obj.set && obj.type && /text/i.test(obj.type)) {
                            var dec = sc.decimals == null ? 1 : sc.decimals;
                            obj.set("text", val.toFixed(dec) + (sc.unit ? " " + sc.unit : ""));
                        }
                        break;
                    case "auto": {
                        // Sembole özel: tint/blink objeyi boyar; su/dönme/gösterge/
                        // akış overlay olarak after:render'da çizilir.
                        var ak = autoKind(obj);
                        if (ak === "tint") {
                            applyColor(obj, (val >= thr) ? (sc.onColor || "#3fbf6f") : (sc.offColor || "#e4544c"));
                        } else if (ak === "blink") {
                            if (val >= thr) obj.set("opacity", 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(now / 130)));
                            else obj.set("opacity", s.opacity);
                        }
                        break;
                    }
                }
            });

            canvas.requestRenderAll();
            raf = requestAnimationFrame(tick);
        }

        // Overlay katmanı: sembolün üstüne (after:render) değere göre canlı çizim.
        function drawOverlays() {
            if (!running) return;
            var ctx = canvas.contextContainer || canvas.getContext();
            var vpt = canvas.viewportTransform || [1, 0, 0, 1, 0, 0];
            var t = nowMs();
            ctx.save();
            ctx.transform(vpt[0], vpt[1], vpt[2], vpt[3], vpt[4], vpt[5]);
            bound().forEach(function (o) {
                var sc = o.scada;
                if (sc.anim !== "auto") return;
                var ak = autoKind(o);
                if (!OVERLAY_KINDS[ak]) return;
                var val = num(tags[sc.tag], 0);
                var ratio = ratioOf(val, num(sc.min, 0), num(sc.max, 100));
                var r = o.getBoundingRect(true, true);
                if (ak === "water") drawWater(ctx, r, ratio, t, false);
                else if (ak === "aeration") drawWater(ctx, r, Math.max(ratio, 0.4), t, true);
                else if (ak === "spin") drawSpin(ctx, r, t, (val >= num(sc.threshold, 1) && val > 0) ? num(sc.speed, 1) : 0);
                else if (ak === "gauge") drawGauge(ctx, r, ratio);
                else if (ak === "flow") {
                    if (val > 0) {
                        var fp = FLOW_PATHS[o.symbolKey];
                        if (fp) drawFlowPath(ctx, o, fp, t);
                        else drawFlow(ctx, r, t, true);
                    }
                }
            });
            ctx.restore();
        }
        canvas.on("after:render", drawOverlays);

        return {
            start: function () {
                if (running) return;
                snaps.clear();
                bound().forEach(snapshot);
                running = true; lastT = 0;
                raf = requestAnimationFrame(tick);
            },
            stop: function () {
                running = false;
                if (raf) cancelAnimationFrame(raf);
                bound().forEach(restore);
                canvas.requestRenderAll();
            },
            isRunning: function () { return running; },
            // HMI buton aksiyonları — bağlı etikete (scada.tag) değer uygular.
            pressButton: function (o) {
                var sc = o.scada || {}; if (!sc.tag) return;
                var act = sc.action || "toggle";
                if (act === "toggle") tags[sc.tag] = (Number(tags[sc.tag]) > 0) ? 0 : 100;
                else if (act === "set") tags[sc.tag] = Number(sc.setValue == null ? 100 : sc.setValue);
                else tags[sc.tag] = Number(sc.pressValue == null ? 100 : sc.pressValue); // momentary
            },
            releaseButton: function (o) {
                var sc = o.scada || {}; if (!sc.tag) return;
                if ((sc.action || "toggle") === "momentary")
                    tags[sc.tag] = Number(sc.releaseValue == null ? 0 : sc.releaseValue);
            },
            setTag: function (name, val) { tags[name] = Number(val); },
            setTags: function (obj) { tags = Object.assign({}, obj); },
            getTags: function () { return tags; },
            tagList: function () {
                var set = {};
                bound().forEach(function (o) { if (o.scada.tag) set[o.scada.tag] = true; });
                return Object.keys(set);
            }
        };
    }

    window.MimicRuntime = MimicRuntime;
})();
