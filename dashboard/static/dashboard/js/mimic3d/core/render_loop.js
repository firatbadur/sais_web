/**
 * mimic3d/core/render_loop.js — ihtiyac-uzerine (on-demand) render dongusu.
 *
 * NEDEN: Klasik `requestAnimationFrame` dongusu bos duran bir sahnede de 60 fps
 * cizer. Mimikler saha PC'lerinde **saatler boyunca acik** kalir; surekli render
 * fani uguldatir, dizustunun pilini bitirir ve ayni makinede kosan Celery
 * worker'indan CPU calar. Bu yuzden frame yalniz (a) bir sey degistiginde
 * (`invalidate`) veya (b) bir surekli-animasyon jetonu tutulurken cizilir.
 *
 * Hedef: bos editor **0 fps**; yalniz deger-eslemeli (colorState/level/text) bir
 * viewer 4 saniyelik poll basina **tam 1 frame**.
 *
 * Jeton tutanlar:
 *   "orbit"   OrbitControls damping oturana dek
 *   "gizmo"   TransformControls surukleme sirasinda
 *   "runtime" runtime.needsFrame() (zaman-bazli animasyon var)
 *   "tween"   kamera gorus-noktasi ucusu
 */

export function makeRenderLoop(renderFn) {
    let dirty = true;
    let raf = 0;
    let running = false;
    const continuous = new Set();

    // Teshis: son 1 sn'de cizilen frame sayisi (durum cubugunda gosterilir).
    let fps = 0;
    let frames = 0;
    let fpsMark = 0;

    function frame(now) {
        raf = 0;
        if (!running) return;

        if (fpsMark === 0) fpsMark = now;
        if (now - fpsMark >= 1000) {
            fps = Math.round((frames * 1000) / (now - fpsMark));
            frames = 0;
            fpsMark = now;
        }

        const needed = dirty || continuous.size > 0;
        if (needed) {
            dirty = false;
            frames += 1;
            renderFn(now);
        } else {
            // Hicbir sey gerekmiyor: fps sayacini sifira dusur ve dongude kal
            // (rAF ucuz; asil maliyet render'dir).
            if (frames === 0) fps = 0;
        }
        raf = requestAnimationFrame(frame);
    }

    return {
        start() {
            if (running) return;
            running = true;
            dirty = true;
            raf = requestAnimationFrame(frame);
        },
        stop() {
            running = false;
            if (raf) cancelAnimationFrame(raf);
            raf = 0;
        },
        /** Bir sey degisti -> tek frame ciz. */
        invalidate() {
            dirty = true;
        },
        requestContinuous(token) {
            if (!continuous.has(token)) {
                continuous.add(token);
                dirty = true;
            }
        },
        releaseContinuous(token) {
            // TUZAK: `dirty = true`'yu kosulsuz yazmak dongunun kendini sonsuza
            // beslemesine yol acar -> render fonksiyonu her frame'de (ornegin
            // OrbitControls oturunca) release cagirir, o da yeni frame ister,
            // o frame yine release cagirir... Sonuc: bos editorde 120 fps, yani
            // on-demand render'in TAM TERSI. Bu yuzden yalniz jeton GERCEKTEN
            // birakildiginda (son bir frame icin) isaretlenir.
            if (continuous.delete(token)) {
                dirty = true;   // animasyonun bitis hali cizilsin
            }
        },
        isContinuous(token) {
            return token === undefined ? continuous.size > 0 : continuous.has(token);
        },
        get fps() {
            return fps;
        },
    };
}
