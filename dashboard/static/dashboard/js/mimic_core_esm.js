/**
 * mimic_core_esm.js — `mimic_core.js` (klasik IIFE) icin ESM adaptoru.
 *
 * 3B moduller `import { autoKind, ratioOf } from "mimic/core";` yazsin diye var;
 * importmap'te "mimic/core" bu dosyaya eslenir.
 *
 * Yukleme sirasi garantili: async OLMAYAN klasik <script> her zaman defer'li
 * `type="module"` script'lerden ONCE calisir -> `window.MimicCore` bu modul
 * degerlendirilirken hazirdir. Yine de acikca kontrol ediyoruz: eksik <script>
 * etiketi 200 satir sonra "undefined is not a function" olarak degil, BURADA
 * yuksek sesle patlasin.
 */
const C = window.MimicCore;
if (!C) {
    throw new Error(
        "mimic_core.js yuklenmedi — 3B sablonunda modul script'inden ONCE "
        + "<script src=\"...dashboard/js/mimic_core.js\"></script> olmali.");
}

export const clamp = C.clamp;
export const ratioOf = C.ratioOf;
export const num = C.num;
export const frac = C.frac;
export const nowMs = C.nowMs;

export const tokenize = C.tokenize;
export const compileExpr = C.compileExpr;
export const evalExpr = C.evalExpr;
export const exprTags = C.exprTags;

export const AUTO_OVERRIDE = C.AUTO_OVERRIDE;
export const autoKind = C.autoKind;
export const OVERLAY_KINDS = C.OVERLAY_KINDS;

export const valueOf = C.valueOf;
export const scadaDefaults = C.scadaDefaults;
export const normalizeScada = C.normalizeScada;

export const TagBag = C.TagBag;
export const pressButton = C.pressButton;
export const releaseButton = C.releaseButton;
export const tagListFrom = C.tagListFrom;

export const ANIM_KINDS_2D = C.ANIM_KINDS_2D;
export const ANIM_KINDS_3D = C.ANIM_KINDS_3D;

export default C;
