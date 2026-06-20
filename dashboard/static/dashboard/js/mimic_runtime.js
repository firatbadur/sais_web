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
                }
            });

            canvas.requestRenderAll();
            raf = requestAnimationFrame(tick);
        }

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
