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
    // Güvenli ifade değerlendirici (çoklu sensör bağlama).
    //   Örn: "Pompa1 || Pompa2"  ·  "pH > 8 && pH < 9"  ·  "(A + B) / 2"
    // Sadece sayı, etiket adı ve şu operatörler: || && ! > < >= <= == != + - * / ( )
    // eval YOK → keyfi JS çalıştırılamaz. Sonuç sayı (doğru=1 / yanlış=0).
    // --------------------------------------------------------------------- //
    var PREC = { "!": 7, "*": 6, "/": 6, "+": 5, "-": 5, ">": 4, "<": 4, ">=": 4, "<=": 4, "==": 3, "!=": 3, "&&": 2, "||": 1 };
    var EXPR_CACHE = {};
    function tokenize(s) {
        var toks = [], i = 0, n = s.length, two, c;
        var two2 = { ">=": 1, "<=": 1, "==": 1, "!=": 1, "&&": 1, "||": 1 };
        while (i < n) {
            c = s.charAt(i);
            if (c === " " || c === "\t") { i++; continue; }
            two = s.substr(i, 2);
            if (two2[two]) { toks.push({ t: "op", v: two }); i += 2; continue; }
            if ("()!+-*/><".indexOf(c) >= 0) { toks.push({ t: "op", v: c }); i++; continue; }
            if ((c >= "0" && c <= "9") || c === ".") {
                var j = i; while (j < n && ((s.charAt(j) >= "0" && s.charAt(j) <= "9") || s.charAt(j) === ".")) j++;
                toks.push({ t: "num", v: parseFloat(s.slice(i, j)) }); i = j; continue;
            }
            if (/[A-Za-z_]/.test(c)) {
                var k = i; while (k < n && /[A-Za-z0-9_]/.test(s.charAt(k))) k++;
                toks.push({ t: "id", v: s.slice(i, k) }); i = k; continue;
            }
            i++;
        }
        return toks;
    }
    function compileExpr(expr) {
        if (EXPR_CACHE[expr] !== undefined) return EXPR_CACHE[expr];
        var toks = tokenize(expr), out = [], ops = [], idx, tk, top;
        try {
            for (idx = 0; idx < toks.length; idx++) {
                tk = toks[idx];
                if (tk.t === "num" || tk.t === "id") out.push(tk);
                else if (tk.v === "(") ops.push(tk);
                else if (tk.v === ")") {
                    while (ops.length && ops[ops.length - 1].v !== "(") out.push(ops.pop());
                    ops.pop();
                } else {
                    while (ops.length) {
                        top = ops[ops.length - 1];
                        if (top.v === "(") break;
                        if (PREC[top.v] > PREC[tk.v] || (PREC[top.v] === PREC[tk.v] && tk.v !== "!")) out.push(ops.pop());
                        else break;
                    }
                    ops.push(tk);
                }
            }
            while (ops.length) out.push(ops.pop());
        } catch (e) { out = null; }
        EXPR_CACHE[expr] = out;
        return out;
    }
    function evalExpr(expr, tags) {
        var rpn = compileExpr(expr); if (!rpn) return 0;
        var st = [], i, tk, a, b, op, r;
        for (i = 0; i < rpn.length; i++) {
            tk = rpn[i];
            if (tk.t === "num") { st.push(tk.v); continue; }
            if (tk.t === "id") { var v = Number(tags[tk.v]); st.push(isNaN(v) ? 0 : v); continue; }
            op = tk.v;
            if (op === "!") { a = st.pop() || 0; st.push(a == 0 ? 1 : 0); continue; }
            b = st.pop() || 0; a = st.pop() || 0;
            switch (op) {
                case "+": r = a + b; break; case "-": r = a - b; break;
                case "*": r = a * b; break; case "/": r = b === 0 ? 0 : a / b; break;
                case ">": r = a > b ? 1 : 0; break; case "<": r = a < b ? 1 : 0; break;
                case ">=": r = a >= b ? 1 : 0; break; case "<=": r = a <= b ? 1 : 0; break;
                case "==": r = a == b ? 1 : 0; break; case "!=": r = a != b ? 1 : 0; break;
                case "&&": r = (a != 0 && b != 0) ? 1 : 0; break;
                case "||": r = (a != 0 || b != 0) ? 1 : 0; break;
                default: r = 0;
            }
            st.push(r);
        }
        return st.length ? st[st.length - 1] : 0;
    }
    function exprTags(expr) {
        return tokenize(expr).filter(function (t) { return t.t === "id"; }).map(function (t) { return t.v; });
    }

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
        flow_cell: "water", sample_cell: "water", sample_fridge: "carousel",
        wash_bar: "washbar"
    };
    function autoKind(o) {
        var k = o.symbolKey || "";
        if (AUTO_OVERRIDE[k]) return AUTO_OVERRIDE[k];
        if (/^(tank|reactor|basin|clarifier|wet_well|grit|weir|open_channel)/.test(k)) return "water";
        if (/^(pump|motor|fan|mixer|blower|compressor|dosing_pump|uf_module|ro_membrane)/.test(k)) return "spin";
        if (/^(pipe|union|flange|expansion|strainer)/.test(k)) return "flow";
        if (/^(valve|solenoid|lamp|pushbutton|switch|ups|plc|cabinet|hmi|rtu|breaker|vfd|generator|solar|energy|level_switch|float)/.test(k)) return "tint";
        return "tint";
    }
    var OVERLAY_KINDS = { water: 1, aeration: 1, spin: 1, gauge: 1, flow: 1, carousel: 1, washbar: 1 };

    // --------------------------------------------------------------------- //
    // Overlay çizimleri (sahne koordinatında; viewport transform uygulanmış halde)
    // --------------------------------------------------------------------- //
    // Suyu, varsa sembolün iç hazne şekline (WATER_SHAPES) KIRPARAK çizer →
    // konik dip / yuvarlak gövde gibi şekillerde su dışarı taşmaz, şekli izler.
    function drawWater(ctx, r, ratio, t, bubbles, shape) {
        var x, w, top, fullH, clipped = false;
        if (shape && shape.length) {
            var minY = 1, maxY = 0, i;
            for (i = 0; i < shape.length; i++) {
                if (shape[i][1] < minY) minY = shape[i][1];
                if (shape[i][1] > maxY) maxY = shape[i][1];
            }
            ctx.save(); clipped = true;
            ctx.beginPath();
            for (i = 0; i < shape.length; i++) {
                var sx = r.left + shape[i][0] * r.width, sy = r.top + shape[i][1] * r.height;
                if (i === 0) ctx.moveTo(sx, sy); else ctx.lineTo(sx, sy);
            }
            ctx.closePath(); ctx.clip();
            x = r.left; w = r.width;
            top = r.top + minY * r.height; fullH = (maxY - minY) * r.height;
        } else {
            x = r.left + r.width * 0.12; w = r.width * 0.76;
            top = r.top + r.height * 0.10; fullH = r.height * 0.82;
        }
        var h = fullH * ratio, y = top + fullH - h;
        var amp = clamp(w * 0.03, 1.5, 5), k = (2 * Math.PI) / (w / 1.25), ph = t / 280;
        var x0 = x - 3, x1 = x + w + 3, bot = top + fullH + 4;
        ctx.beginPath(); ctx.moveTo(x0, y);
        for (var px = -3; px <= w + 3; px += 3) ctx.lineTo(x + px, y + Math.sin(px * k + ph) * amp);
        ctx.lineTo(x1, bot); ctx.lineTo(x0, bot); ctx.closePath();
        ctx.fillStyle = "rgba(47,155,214,0.6)"; ctx.fill();
        ctx.strokeStyle = "rgba(150,210,240,0.9)"; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(x0, y);
        for (px = -3; px <= w + 3; px += 3) ctx.lineTo(x + px, y + Math.sin(px * k + ph) * amp);
        ctx.stroke();
        if (bubbles && h > 4) {
            ctx.fillStyle = "rgba(255,255,255,0.7)";
            for (var b = 0; b < 9; b++) {
                var seed = b * 1.61;
                var bx = x + w * (0.08 + 0.84 * frac(seed * 0.37));
                var prog = frac(t / 1500 + b * 0.19);
                var by = top + fullH - prog * (h - 2);
                ctx.beginPath(); ctx.arc(bx, by, 1.4 + 1.6 * frac(seed), 0, 2 * Math.PI); ctx.fill();
            }
        }
        if (clipped) ctx.restore();
    }

    // Tank iç hazne şekilleri (normalize 0..1, sembolün sınır kutusuna göre) —
    // su bu çokgene kırpılır. SVG gövde yollarından türetildi.
    var WATER_SHAPES = {
        tank_vertical: [[0.111, 0.2], [0.5, 0.05], [0.889, 0.2], [0.889, 0.833], [0.5, 0.95], [0.111, 0.833]],
        tank_horizontal: [[0.154, 0.143], [0.846, 0.143], [0.954, 0.5], [0.846, 0.857], [0.154, 0.857], [0.046, 0.5]],
        tank_cone: [[0.14, 0.154], [0.86, 0.154], [0.86, 0.662], [0.5, 0.938], [0.14, 0.662]],
        reactor: [[0.16, 0.262], [0.5, 0.138], [0.84, 0.262], [0.84, 0.738], [0.5, 0.892], [0.16, 0.738]],
        basin: [[0.057, 0.125], [0.943, 0.125], [0.871, 0.9], [0.129, 0.9]],
        clarifier: [[0.054, 0.222], [0.946, 0.222], [0.946, 0.622], [0.5, 0.8], [0.054, 0.622]],
        clarifier_round: [[0.05, 0.222], [0.95, 0.222], [0.5, 0.867]],
        wet_well: [[0.12, 0.1], [0.88, 0.1], [0.88, 0.76], [0.5, 0.92], [0.12, 0.76]],
        grit_chamber: [[0.067, 0.15], [0.933, 0.15], [0.933, 0.6], [0.617, 0.875], [0.383, 0.875], [0.067, 0.6]],
        weir: [[0.055, 0.2], [0.418, 0.2], [0.5, 0.429], [0.582, 0.2], [0.945, 0.2], [0.945, 0.886], [0.055, 0.886]],
        open_channel: [[0.185, 0.167], [0.815, 0.167], [0.815, 0.733], [0.185, 0.733]],
        aeration: [[0.062, 0.167], [0.938, 0.167], [0.938, 0.881], [0.062, 0.881]],
        flow_cell: [[0.314, 0.062], [0.686, 0.062], [0.686, 0.938], [0.314, 0.938]],
        sample_cell: [[0.225, 0.156], [0.775, 0.156], [0.775, 0.844], [0.225, 0.844]]
    };
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
    // Numune dolabı şişe karuseli — numune alma aktifken döner. Statik şişeleri
    // örtmek için iç diski boyar, sonra dönen şişe halkası + göbek çizer.
    function drawCarousel(ctx, r, t, speed) {
        if (speed <= 0) return;
        var cx = r.left + r.width * 0.5, cy = r.top + r.height * (80 / 130);
        var maskR = r.width * (25 / 90), ringR = r.width * (18 / 90), bR = r.width * (4.5 / 90);
        ctx.save();
        ctx.beginPath(); ctx.arc(cx, cy, maskR, 0, 2 * Math.PI);
        ctx.fillStyle = "#aeb9c4"; ctx.fill();
        ctx.strokeStyle = "#5e6b7a"; ctx.lineWidth = Math.max(1, r.width * 0.02);
        ctx.beginPath(); ctx.arc(cx, cy, ringR + bR + 1, 0, 2 * Math.PI); ctx.stroke();
        var ang = (t / 1000) * speed * 1.1;
        for (var i = 0; i < 8; i++) {
            var a = ang + i * Math.PI / 4;
            var bx = cx + Math.cos(a) * ringR, by = cy + Math.sin(a) * ringR;
            ctx.beginPath(); ctx.arc(bx, by, bR, 0, 2 * Math.PI);
            ctx.fillStyle = "#ffffff"; ctx.fill(); ctx.stroke();
        }
        ctx.beginPath(); ctx.arc(cx, cy, bR * 1.2, 0, 2 * Math.PI);
        ctx.fillStyle = "#93a1b0"; ctx.fill(); ctx.stroke();
        ctx.restore();
    }
    // Yıkama çubuğu — aktifken (yıkama true) deliklerinden iki yana su fışkırtır;
    // sweep (zamanla değişen menzil) çubuğun dönüşünü ima eder.
    function drawWashBar(ctx, r, t, speed) {
        if (speed <= 0) return;
        var cx = r.left + r.width * 0.5;
        var rodHalf = r.width * 0.1;
        var maxReach = r.width * 1.6 * speed;
        var holes = [0.19, 0.31, 0.43, 0.56, 0.68, 0.8];
        ctx.fillStyle = "rgba(47,155,214,0.85)";
        for (var hI = 0; hI < holes.length; hI++) {
            var y0 = r.top + holes[hI] * r.height;
            var sweep = 0.55 + 0.45 * Math.abs(Math.sin(t / 240 + hI * 1.3));
            for (var side = -1; side <= 1; side += 2) {
                for (var d = 0; d < 4; d++) {
                    var prog = frac(t / 480 + d * 0.25 + (side > 0 ? 0.12 : 0));
                    var dist = prog * maxReach * sweep;
                    var dx = cx + side * (rodHalf + dist);
                    var dy = y0 + dist * 0.3 * prog + 2;
                    ctx.globalAlpha = 0.9 * (1 - prog);
                    ctx.beginPath();
                    ctx.arc(dx, dy, 2.4 * (1 - prog) + 0.6, 0, 2 * Math.PI);
                    ctx.fill();
                }
            }
        }
        ctx.globalAlpha = 1;
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
    // Akışı objenin GÖRÜNEN sınır kutusuna (getBoundingRect) oturtarak çiz —
    // grup iç koordinatlarına bağlı değil, eksen-hizalı yerleşimde kesin doğru.
    function drawFlowPath(ctx, r, paths, t) {
        function P(n) { return { x: r.left + n[0] * r.width, y: r.top + n[1] * r.height }; }
        var lw = Math.max(3, Math.min(r.width, r.height) * 0.34);
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
        // Objenin sürücü değeri: ifade (çoklu sensör) varsa onu değerlendir,
        // yoksa tek etiketin değeri.
        function valueOf(sc) {
            return (sc.expr && sc.expr.trim()) ? evalExpr(sc.expr, tags) : num(tags[sc.tag], 0);
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
                var val = valueOf(sc);
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
                            var suffix = (sc.showUnit !== false && sc.unit) ? " " + sc.unit : "";
                            obj.set("text", val.toFixed(dec) + suffix);
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
                var val = valueOf(sc);
                var ratio = ratioOf(val, num(sc.min, 0), num(sc.max, 100));
                var r = o.getBoundingRect(true, true);
                var wshape = o._objects ? WATER_SHAPES[o.symbolKey] : null;
                if (ak === "water") drawWater(ctx, r, ratio, t, false, wshape);
                else if (ak === "aeration") drawWater(ctx, r, Math.max(ratio, 0.4), t, true, wshape);
                else if (ak === "spin") drawSpin(ctx, r, t, (val >= num(sc.threshold, 1) && val > 0) ? num(sc.speed, 1) : 0);
                else if (ak === "carousel") drawCarousel(ctx, r, t, (val >= num(sc.threshold, 1) && val > 0) ? num(sc.speed, 1) : 0);
                else if (ak === "washbar") drawWashBar(ctx, r, t, (val >= num(sc.threshold, 1) && val > 0) ? num(sc.speed, 1) : 0);
                else if (ak === "gauge") drawGauge(ctx, r, ratio);
                else if (ak === "flow") {
                    if (val > 0) {
                        var fp = FLOW_PATHS[o.symbolKey];
                        if (fp) drawFlowPath(ctx, r, fp, t);
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
                bound().forEach(function (o) {
                    var sc = o.scada;
                    if (sc.expr && sc.expr.trim()) exprTags(sc.expr).forEach(function (t) { set[t] = true; });
                    else if (sc.tag) set[sc.tag] = true;
                });
                return Object.keys(set);
            }
        };
    }

    window.MimicRuntime = MimicRuntime;
})();
