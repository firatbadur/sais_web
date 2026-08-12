/**
 * mimic3d/core/history.js — undo/redo: TAM BELGE snapshot'lari.
 *
 * Komut-pattern (her mutasyonun tersini yazmak) yerine snapshot secildi:
 *  1. 2B editorle AYNI zihinsel model (pushUndo/restoreFrom) -> iki editor
 *     arasinda gecen bakimci ayni sekli gorur.
 *  2. Komut-pattern ~40 mutasyon noktasi (transform, material, scada, reparent,
 *     rename, gorunurluk, kilit, geometri parametreleri, sahne ayarlari, kamera,
 *     gorus noktalari...) icin dogru inverse ister; undo hatalari tam burada
 *     ureter ve bunlari yakalayacak test suiti yok.
 *  3. Restore yolu = "belgeyi yukle" ile ayni kod -> tek yol, iki kat test.
 *
 * Maliyet durustce yonetilir: 300 nesneli belge ~250 KB; cap 40 kayit VE 24 MB
 * bayt butcesi (hangisi once dolarsa en eskiyi at). Ayrica gizmo suruklemesinde
 * frame basina DEGIL, mouseDown'da TEK snapshot alinir.
 */

export function makeHistory(opts) {
    const options = opts || {};
    const MAX = options.max || 40;
    const MAX_BYTES = options.maxBytes || 24 * 1024 * 1024;
    const snapshot = options.snapshot;      // () => string (JSON)
    const restore = options.restore;        // (string) => void
    const onChange = options.onChange || function () {};

    let undoStack = [];
    let redoStack = [];
    let bytes = 0;

    function trim() {
        while (undoStack.length > MAX || (bytes > MAX_BYTES && undoStack.length > 1)) {
            bytes -= undoStack.shift().length;
        }
    }

    function push() {
        const s = snapshot();
        // Ayni durumu iki kez yigina koyma (gereksiz undo adimi olusmasin).
        if (undoStack.length && undoStack[undoStack.length - 1] === s) return;
        undoStack.push(s);
        bytes += s.length;
        redoStack = [];
        trim();
        onChange();
    }

    return {
        /** Ilk durumu kaydet (belge yuklendikten sonra). */
        reset() {
            const s = snapshot();
            undoStack = [s];
            redoStack = [];
            bytes = s.length;
            onChange();
        },
        push: push,
        undo() {
            if (undoStack.length < 2) return false;
            const cur = undoStack.pop();
            bytes -= cur.length;
            redoStack.push(cur);
            restore(undoStack[undoStack.length - 1]);
            onChange();
            return true;
        },
        redo() {
            if (!redoStack.length) return false;
            const s = redoStack.pop();
            undoStack.push(s);
            bytes += s.length;
            restore(s);
            trim();
            onChange();
            return true;
        },
        canUndo() { return undoStack.length > 1; },
        canRedo() { return redoStack.length > 0; },
        depth() { return undoStack.length - 1; },
        bytes() { return bytes; },
    };
}
