/**
 * mimic3d/io/screenshot.js — PNG dısa aktarma + galeri thumbnail'i.
 *
 * KRITIK KISIT: renderer `preserveDrawingBuffer: false` ile kurulur (performans
 * varsayilani). Bu, framebuffer'in frame sonunda GECERSIZ olmasi demektir ->
 * render ile `toDataURL()` AYNI TICK'te olmali. Araya bir `await` girerse
 * (kod "daha temiz" gorunse de) sonuc BOS/SIYAH bir PNG'dir. Bu fonksiyonlarin
 * hepsi bilincli olarak SENKRONDUR.
 *
 * Cekim sirasinda `helperRoot` (izgara + gizmo) gizlenir; `bgRoot` (gokyuzu) ve
 * zemin GORUNUR kalir — onlar tasarimin parcasidir.
 */

import { LIMITS } from "../core/doc.js";
import { rescaleLabel } from "../core/factory.js";

function labelsOf(stage) {
    const out = [];
    stage.contentRoot.traverse((o) => {
        if (o.userData && o.userData.labelCanvas && o.visible !== false) out.push(o);
    });
    return out;
}

export function makeScreenshot(stage, loop) {
    /**
     * @param {number} w,h piksel
     * @param {string} mime "image/png" | "image/jpeg"
     * @param {number} [quality] jpeg icin
     * @param {number} [labelMult] etiketleri bu katta yeniden ciz (keskinlik)
     */
    function capture(w, h, mime, quality, labelMult) {
        const r = stage.renderer;
        const oldSize = { x: r.domElement.width, y: r.domElement.height };
        const oldPR = r.getPixelRatio();
        const oldAspect = stage.camera.aspect;
        const helperVisible = stage.helperRoot.visible;

        // Piksel tavani: zayif Intel iGPU'larda cok buyuk renderbuffer
        // olusturmak baglami dusurur -> sessiz siyah kare yerine kirp.
        const maxPx = LIMITS.maxCaptureMegapixels * 1e6;
        let W = Math.max(16, Math.round(w));
        let H = Math.max(16, Math.round(h));
        let clamped = false;
        if (W * H > maxPx) {
            const k = Math.sqrt(maxPx / (W * H));
            W = Math.floor(W * k);
            H = Math.floor(H * k);
            clamped = true;
        }

        const labels = labelMult && labelMult > 1 && labelsOf(stage).length <= 40
            ? labelsOf(stage) : [];
        labels.forEach((o) => rescaleLabel(o, labelMult));

        stage.helperRoot.visible = false;
        r.setPixelRatio(1);
        r.setSize(W, H, false);
        stage.camera.aspect = W / H;
        stage.camera.updateProjectionMatrix();

        // Composer'i (outline) BYPASS et: secim vurgusu cikti goruntude olmamali.
        r.render(stage.scene, stage.camera);
        const url = r.domElement.toDataURL(mime, quality);   // AYNI TICK

        // Geri al
        labels.forEach((o) => rescaleLabel(o, 1));
        stage.helperRoot.visible = helperVisible;
        r.setPixelRatio(oldPR);
        r.setSize(oldSize.x / oldPR, oldSize.y / oldPR, false);
        stage.camera.aspect = oldAspect;
        stage.camera.updateProjectionMatrix();
        stage.resize();
        loop.invalidate();

        return { url: url, width: W, height: H, clamped: clamped };
    }

    return {
        capture: capture,
        /** Tasarim cozunurlugunun `mult` kati PNG. */
        exportPNG(designW, designH, mult) {
            const m = Math.max(1, Math.min(4, mult || 2));
            return capture(designW * m, designH * m, "image/png", undefined, m);
        },
        /**
         * Galeri thumbnail'i — JPEG (PNG DEGIL): 3B render fotografiktir, ayni
         * kalitede JPEG ~5 kat kucuktur ve `thumbnail` alani (TextField) her
         * galeri sorgusunda tasinir.
         */
        thumbnail() {
            return capture(480, 270, "image/jpeg", 0.82, 1).url;
        },
    };
}
