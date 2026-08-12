/* ===========================================================================
 * mimic_core.js — 2B (Fabric.js) ve 3B (Three.js) mimik motorlarinin
 * PAYLASILAN, renderer'dan BAGIMSIZ cekirdegi.
 * ---------------------------------------------------------------------------
 * NEDEN PAYLASIM (kopyalama degil): 3B sembol kutuphanesi 2B ile AYNI
 * `symbolKey` isim alanini kullanir. Iki ayri `autoKind`/`AUTO_OVERRIDE`
 * kopyasi tutulsa, birine sembol eklenip digerine eklenmediginde ayni mimik
 * iki renderer'da FARKLI animasyon gosterirdi -- log'suz, hatasiz, sessiz.
 * HMI'da yanlis gosterim bu urunde en kotu hata sinifidir; ~200 satirlik
 * kopya ise sadece cirkin. Bu yuzden semantik tek yerde yasar.
 *
 * Icerik (hepsi saf fonksiyon / veri; DOM, Fabric veya Three BAGIMLILIGI YOK):
 *   - sayisal yardimcilar            clamp / ratioOf / num / frac / nowMs
 *   - guvenli ifade motoru           tokenize / compileExpr / evalExpr / exprTags
 *   - sembol siniflandirmasi         AUTO_OVERRIDE / autoKind / OVERLAY_KINDS
 *   - baglama yardimcilari           valueOf / SCADA_DEFAULTS / normalizeScada
 *   - etiket torbasi + buton         TagBag / pressButton / releaseButton
 *   - UI meta verisi                 ANIM_KINDS_2D / ANIM_KINDS_3D
 *
 * Yukleme: klasik <script> (window.MimicCore). 3B tarafi ESM adaptoru
 * `mimic_core_esm.js` uzerinden `import { autoKind } from "mimic/core"` yazar.
 * mimic_runtime.js'den ONCE yuklenmelidir.
 * ======================================================================== */
(function () {
    "use strict";

    // --------------------------------------------------------------------- //
    // Sayisal yardimcilar
    // --------------------------------------------------------------------- //
    function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
    function ratioOf(val, mn, mx) {
        if (mx === mn) return 0;
        return clamp((val - mn) / (mx - mn), 0, 1);
    }
    function num(v, d) { v = Number(v); return isNaN(v) ? d : v; }
    function frac(x) { return x - Math.floor(x); }
    function nowMs() {
        return (window.performance && performance.now) ? performance.now() : Date.now();
    }

    // --------------------------------------------------------------------- //
    // Guvenli ifade degerlendirici (coklu sensor baglama).
    //   Orn: "Pompa1 || Pompa2"  ·  "pH > 8 && pH < 9"  ·  "(A + B) / 2"
    // Sadece sayi, etiket adi ve su operatorler: || && ! > < >= <= == != + - * / ( )
    // eval YOK -> keyfi JS calistirilamaz. Sonuc sayi (dogru=1 / yanlis=0).
    // --------------------------------------------------------------------- //
    var PREC = {
        "!": 7, "*": 6, "/": 6, "+": 5, "-": 5,
        ">": 4, "<": 4, ">=": 4, "<=": 4, "==": 3, "!=": 3, "&&": 2, "||": 1,
    };
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
                var j = i;
                while (j < n && ((s.charAt(j) >= "0" && s.charAt(j) <= "9") || s.charAt(j) === ".")) j++;
                toks.push({ t: "num", v: parseFloat(s.slice(i, j)) }); i = j; continue;
            }
            if (/[A-Za-z_]/.test(c)) {
                var k = i;
                while (k < n && /[A-Za-z0-9_]/.test(s.charAt(k))) k++;
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
                case "+": r = a + b; break;
                case "-": r = a - b; break;
                case "*": r = a * b; break;
                case "/": r = b === 0 ? 0 : a / b; break;
                case ">": r = a > b ? 1 : 0; break;
                case "<": r = a < b ? 1 : 0; break;
                case ">=": r = a >= b ? 1 : 0; break;
                case "<=": r = a <= b ? 1 : 0; break;
                case "==": r = a == b ? 1 : 0; break;
                case "!=": r = a != b ? 1 : 0; break;
                case "&&": r = (a != 0 && b != 0) ? 1 : 0; break;
                case "||": r = (a != 0 || b != 0) ? 1 : 0; break;
                default: r = 0;
            }
            st.push(r);
        }
        return st.length ? st[st.length - 1] : 0;
    }

    function exprTags(expr) {
        return tokenize(expr)
            .filter(function (t) { return t.t === "id"; })
            .map(function (t) { return t.v; });
    }

    // --------------------------------------------------------------------- //
    // Sembole ozel ("auto") animasyon siniflandirmasi — symbolKey'e gore.
    // 2B'de overlay cizimi, 3B'de mesh/material surumu ile karsiligi vardir;
    // SINIFLANDIRMA IKISINDE DE AYNIDIR (bu dosyanin varlik sebebi).
    //   water   : tank/havuz  -> seviye + dalga
    //   aeration: su + yukselen kabarciklar
    //   spin    : pompa/motor/fan -> donen rotor
    //   gauge   : gosterge ibresi
    //   flow    : boru -> akis
    //   tint    : vana/lamba/pano -> durum rengi
    //   blink   : alarm/cakar -> yanip sonme
    //   carousel/washbar/door: numune buzdolabi / yikama bari / kapi
    // --------------------------------------------------------------------- //
    var AUTO_OVERRIDE = {
        gauge: "gauge", rotameter: "gauge", transmitter: "tint", analyzer: "tint",
        aeration: "aeration", flow_arrow: "flow", screw_conveyor: "flow",
        beacon: "blink", alarm_horn: "blink", emergency_stop: "blink",
        gas_detector: "blink", lamp: "tint", value_display: "tint",
        flow_cell: "water", sample_cell: "water", sample_fridge: "carousel",
        wash_bar: "washbar", door: "door",
    };

    /**
     * @param {object|string} o  `symbolKey` tasiyan obje (Fabric obj / Three
     *        Group / {symbolKey}) veya dogrudan anahtar dizesi.
     */
    function autoKind(o) {
        var k = (typeof o === "string") ? o : ((o && (o.symbolKey
            || (o.userData && o.userData.symbolKey))) || "");
        if (AUTO_OVERRIDE[k]) return AUTO_OVERRIDE[k];
        if (/^(tank|reactor|basin|clarifier|wet_well|grit|weir|open_channel)/.test(k)) return "water";
        if (/^(pump|motor|fan|mixer|blower|compressor|dosing_pump|uf_module|ro_membrane)/.test(k)) return "spin";
        if (/^(pipe|union|flange|expansion|strainer)/.test(k)) return "flow";
        if (/^(valve|solenoid|lamp|pushbutton|switch|ups|plc|cabinet|hmi|rtu|breaker|vfd|generator|solar|energy|level_switch|float)/.test(k)) return "tint";
        return "tint";
    }

    /** 2B'de after:render overlay'i ile cizilen turler (tint/blink ozellik mutasyonudur). */
    var OVERLAY_KINDS = { water: 1, aeration: 1, spin: 1, gauge: 1, flow: 1, carousel: 1, washbar: 1, door: 1 };

    // --------------------------------------------------------------------- //
    // Baglama (scada) yardimcilari
    // --------------------------------------------------------------------- //

    /** Objenin surucu degeri: ifade (coklu sensor) varsa onu, yoksa tek etiketi. */
    function valueOf(sc, tags) {
        if (!sc) return 0;
        return (sc.expr && sc.expr.trim()) ? evalExpr(sc.expr, tags) : num(tags[sc.tag], 0);
    }

    /**
     * Varsayilan `scada` blogu.
     * @param {string} [kind] "2d" | "3d" — TEK FARK `moveRange`: 2B'de piksel
     *        (60), 3B'de sahne birimi/metre (1.0). Ayni sayiyi paylasmak 3B'de
     *        vanayi 60 metre otelerdi.
     */
    function scadaDefaults(kind) {
        return {
            tag: "", expr: "", anim: "none",
            min: 0, max: 100, threshold: 1,
            onColor: "#3fbf6f", offColor: "#e4544c",
            speed: 1, unit: "", decimals: 1, showUnit: true,
            moveRange: (kind === "3d") ? 1.0 : 60,
            menu: { enabled: false, historic: false, report: false, daily: false, control: false },
        };
    }

    /** Eksik anahtarlari varsayilanlarla dolduran kopya dondurur (mutasyon yok). */
    function normalizeScada(sc, kind) {
        var out = scadaDefaults(kind);
        if (sc) {
            Object.keys(sc).forEach(function (k) {
                if (k === "menu") return;
                if (sc[k] !== undefined) out[k] = sc[k];
            });
            if (sc.menu) out.menu = Object.assign(out.menu, sc.menu);
        }
        return out;
    }

    // --------------------------------------------------------------------- //
    // Etiket torbasi + HMI buton semantigi
    // --------------------------------------------------------------------- //
    function TagBag(initial) {
        var tags = Object.assign({}, initial || {});
        return {
            get raw() { return tags; },
            set: function (name, val) { tags[name] = Number(val); },
            setAll: function (obj) { tags = Object.assign({}, obj); },
            reset: function () { tags = {}; },
            all: function () { return tags; },
            value: function (sc) { return valueOf(sc, tags); },
        };
    }

    /**
     * HMI butonuna basildi: bagli etikete deger uygula.
     * Yalniz YEREL etiket haritasini degistirir (POST yapmaz); viewer'da 4 sn'lik
     * canli poll degeri gercekle tekrar ezer.
     */
    function pressButton(sc, tags) {
        if (!sc || !sc.tag) return;
        var act = sc.action || "toggle";
        if (act === "toggle") tags[sc.tag] = (Number(tags[sc.tag]) > 0) ? 0 : 100;
        else if (act === "set") tags[sc.tag] = Number(sc.setValue == null ? 100 : sc.setValue);
        else tags[sc.tag] = Number(sc.pressValue == null ? 100 : sc.pressValue); // momentary
    }

    function releaseButton(sc, tags) {
        if (!sc || !sc.tag) return;
        if ((sc.action || "toggle") === "momentary") {
            tags[sc.tag] = Number(sc.releaseValue == null ? 0 : sc.releaseValue);
        }
    }

    /**
     * Bagli nesnelerin surdugu etiket adlari (sim paneli + canli poll aboneligi).
     * @param {Array} items `.scada` tasiyan nesneler
     */
    function tagListFrom(items) {
        var set = {};
        (items || []).forEach(function (o) {
            var sc = o && o.scada;
            if (!sc) return;
            if (sc.expr && sc.expr.trim()) {
                exprTags(sc.expr).forEach(function (t) { set[t] = true; });
            } else if (sc.tag) {
                set[sc.tag] = true;
            }
        });
        return Object.keys(set);
    }

    // --------------------------------------------------------------------- //
    // 2B belge onarimi (Fabric) — YUKLEMEDEN ONCE cagrilir.
    // --------------------------------------------------------------------- //
    /**
     * Fabric 5.3 tuzagi: JSON'da `styles` ANAHTARI YOKSA yuklenen metin objesinde
     * `styles` **undefined** kalir (bos nesneye varsayilmaz). Sonrasinda HER
     * `toObject()` cagrisi `fabric.util.stylesToArray` icinde
     * "Cannot read properties of undefined (reading '0')" ile patlar.
     *
     * Bu; kaydetme (`canvas.toJSON`), PNG/SVG/JSON disa aktarma, kopyala-yapistir
     * ve editorun yukleme sonrasi ilk `pushUndo()` cagrisini birden bozar —
     * yukleme zinciri yarida kesildigi icin ekran "0 nesne" gorunur.
     *
     * Python tarafinda uretilen belgeler (seed_mimic_templates) `styles` yazmadigi
     * icin YERLESIK SABLON bu yuzden editorde acilamiyordu. Kaynak duzeltildi;
     * bu fonksiyon eski/harici kayitlari da yuklemede guvenli hale getirir.
     *
     * @param {object} data canvas.toJSON() bicimindeki belge (yerinde onarilir)
     * @returns {number} onarilan obje sayisi
     */
    function repair2dDocument(data) {
        var fixed = 0;
        function walk(list) {
            (list || []).forEach(function (o) {
                if (!o || typeof o !== "object") return;
                if (typeof o.type === "string" && /text/i.test(o.type)
                    && (o.styles === undefined || o.styles === null)) {
                    o.styles = {};
                    fixed += 1;
                }
                if (Array.isArray(o.objects)) walk(o.objects);   // grup icleri
            });
        }
        if (data && Array.isArray(data.objects)) walk(data.objects);
        return fixed;
    }

    // --------------------------------------------------------------------- //
    // UI meta verisi — animasyon <select>'lerini besler.
    // --------------------------------------------------------------------- //
    var ANIM_KINDS_2D = [
        "auto", "none", "colorState", "blink", "rotate", "level",
        "fillThreshold", "visibility", "opacity", "moveX", "moveY", "text",
    ];
    /** 3B: 2B'nin tamami + 3B'ye ozgu olanlar (bkz. mimic3d_runtime.js). */
    var ANIM_KINDS_3D = ANIM_KINDS_2D.concat([
        "moveZ", "rotateAxis", "scaleAxis", "emissive", "tilt", "pathFollow", "cameraTour",
    ]);

    window.MimicCore = {
        // sayisal
        clamp: clamp, ratioOf: ratioOf, num: num, frac: frac, nowMs: nowMs,
        // ifade motoru
        PREC: PREC, EXPR_CACHE: EXPR_CACHE,
        tokenize: tokenize, compileExpr: compileExpr, evalExpr: evalExpr, exprTags: exprTags,
        // siniflandirma
        AUTO_OVERRIDE: AUTO_OVERRIDE, autoKind: autoKind, OVERLAY_KINDS: OVERLAY_KINDS,
        // baglama
        valueOf: valueOf, scadaDefaults: scadaDefaults, normalizeScada: normalizeScada,
        // 2B belge onarimi
        repair2dDocument: repair2dDocument,
        // etiket + buton
        TagBag: TagBag, pressButton: pressButton, releaseButton: releaseButton,
        tagListFrom: tagListFrom,
        // UI
        ANIM_KINDS_2D: ANIM_KINDS_2D, ANIM_KINDS_3D: ANIM_KINDS_3D,
    };
})();
