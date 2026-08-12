/**
 * mimic3d/core/dispose.js — GPU kaynak temizligi.
 *
 * Three.js'te `scene.remove(obj)` GPU belleginden HICBIR SEY birakmaz;
 * geometry/material/texture'lar acikca `dispose()` edilmezse WebGL
 * context'inde birikir. Uzun oturumlarda (bir HMI ekrani saatlerce acik durur,
 * tasarimci yuzlerce nesne ekleyip siler, undo/redo sahneyi bastan kurar) bu,
 * "bir sure sonra tarayici yavasliyor / sekme cokuyor" seklinde ortaya cikar.
 *
 * Bu yuzden SILME/YENIDEN KURMA yollarinin HEPSI disposeObject'ten gecer:
 * sil, kes, undo/redo restore, belge yukle/yeni, ungroup (bosalan Group),
 * sembol degistir, beforeunload.
 *
 * PAYLASILAN KAYNAKLAR: sembol kutuphanesi (mimic3d_symbols.js) palet
 * material/geometry/texture'larini TUM ornekler arasinda paylasir ve
 * `userData.shared = true` ile isaretler. Bunlar sayfa yasami boyunca yasar,
 * ASLA dispose edilmez -- yoksa bir pompayi silmek digerlerinin materyalini de
 * yok eder (siyah/kayip mesh).
 */

const MAP_KEYS = [
    "map", "alphaMap", "aoMap", "bumpMap", "displacementMap", "emissiveMap",
    "envMap", "lightMap", "metalnessMap", "normalMap", "roughnessMap",
    "specularMap", "clearcoatMap", "clearcoatNormalMap", "sheenColorMap",
];

function materialsOf(o) {
    if (!o.material) return [];
    return Array.isArray(o.material) ? o.material : [o.material];
}

/** Tek bir material'i (ve sahibi oldugu texture'lari) serbest birak. */
export function disposeMaterial(m) {
    if (!m || (m.userData && m.userData.shared)) return;
    MAP_KEYS.forEach((k) => {
        const t = m[k];
        if (t && t.dispose && !(t.userData && t.userData.shared)) t.dispose();
    });
    m.dispose();
}

/**
 * Bir alt agacin TUM GPU kaynaklarini serbest birakir ve ebeveyninden ayirir.
 * Paylasilan (userData.shared) geometry/material/texture atlanir.
 */
export function disposeObject(root) {
    if (!root) return;
    root.traverse((o) => {
        if (o.geometry && !(o.geometry.userData && o.geometry.userData.shared)) {
            o.geometry.dispose();
        }
        materialsOf(o).forEach(disposeMaterial);
    });
    if (root.parent) root.parent.remove(root);
}

/** Bir kokun TUM cocuklarini dispose ederek bosaltir (belge yukleme/undo). */
export function clearChildren(root) {
    if (!root) return;
    // Kopyayla don: dispose sirasinda children dizisi degisiyor.
    root.children.slice().forEach((c) => disposeObject(c));
}

/**
 * Paylasilan material'lari bu nesneye ozel kopyalara cevirir.
 *
 * KRITIK: sembol builder'lari palet material'larini PAYLASIR. `colorState`
 * animasyonu ya da kullanicinin renk override'i paylasilan material'i
 * degistirirse **bir pompayi yesile boyamak butun pompalari yesile boyar**.
 * 3B'ye ozgu, sessiz ve kafa karistirici bir hata sinifidir; bu yuzden
 * material'a DOKUNAN her yol (inspector rengi, runtime tint/blink/opacity)
 * once bunu cagirir. Tembel yapilir: dokunulmayan nesne paylasmaya devam eder
 * (bellek + draw-call dostu).
 *
 * @returns {number} klonlanan material sayisi
 */
export function ensureOwnMaterials(obj) {
    let n = 0;
    if (!obj) return n;
    obj.traverse((o) => {
        if (!o.material) return;
        if (Array.isArray(o.material)) {
            o.material = o.material.map((m) => {
                if (m && m.userData && m.userData.shared) {
                    const c = m.clone();
                    c.userData = Object.assign({}, m.userData, { shared: false });
                    n += 1;
                    return c;
                }
                return m;
            });
        } else if (o.material.userData && o.material.userData.shared) {
            const c = o.material.clone();
            c.userData = Object.assign({}, o.material.userData, { shared: false });
            o.material = c;
            n += 1;
        }
    });
    return n;
}

/** Sahne + renderer'i tamamen serbest birak (sayfadan cikis). */
export function disposeStage(stage) {
    if (!stage) return;
    clearChildren(stage.contentRoot);
    stage.dispose();
    try {
        stage.renderer.forceContextLoss();
    } catch (e) { /* bazi tarayicilar desteklemez */ }
}
