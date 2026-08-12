/**
 * mimic3d/io/persist.js — kaydet / ac / disa-ice aktar.
 *
 * Sunucu sozlesmesi 2B ile AYNI endpoint'lerdir (api_mimic_save/get/list);
 * tek fark payload'a `kind: "3d"` eklenmesidir. `kind` sunucuda YALNIZ
 * olusturmada yazilir, sonradan degistirilemez (bkz. mimic_screen_save).
 */

import { LIMITS } from "../core/doc.js";

function download(filename, text, mime) {
    const blob = new Blob([text], { type: mime || "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function makePersist(cfg, opts) {
    const options = opts || {};
    const getDoc = options.getDoc;           // () => doc
    const getName = options.getName;         // () => string
    const getDesc = options.getDesc;         // () => string
    const thumbnail = options.thumbnail;     // () => dataURL
    const loadDoc = options.loadDoc;         // (doc) => void
    const toast = options.toast || function () {};
    const setId = options.setId || function () {};
    const clearDirty = options.clearDirty || function () {};

    let screenId = cfg.screenId || null;

    function payload(asNew) {
        const doc = getDoc();
        return {
            id: asNew ? null : screenId,
            kind: "3d",
            name: (getName() || "").trim() || "Adsız 3B Sahne",
            description: getDesc ? (getDesc() || "") : "",
            width: options.width || 1280,
            height: options.height || 720,
            // `background` kolonu galeri kartinin ve viewer chrome'unun rengi;
            // sahne gradient'inin taban rengini yansitir.
            background: (doc.scene && doc.scene.background
                && (doc.scene.background.bottom || doc.scene.background.color)) || "#141b26",
            data: doc,
            thumbnail: thumbnail ? thumbnail() : "",
        };
    }

    function save(asNew) {
        const doc = getDoc();
        const n = (doc.objects || []).length;
        if (n > LIMITS.maxObjects) {
            toast("Sahnedeki nesne sayısı üst sınırı aşıyor (" + n + " / "
                + LIMITS.maxObjects + "). Kaydetmeden önce sahneyi bölün.", "danger");
            return Promise.resolve(false);
        }
        const p = payload(asNew);
        return fetch(cfg.urls.save, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRFToken": cfg.csrf },
            body: JSON.stringify(p),
        }).then((r) => r.json().then((d) => ({ status: r.status, d: d })))
            .then((res) => {
                if (!res.d || !res.d.ok) {
                    toast((res.d && res.d.error) || "Kayıt hatası", "danger");
                    return false;
                }
                screenId = res.d.id;
                setId(screenId);
                clearDirty();
                toast("3B sahne kaydedildi.", "success");
                if (history.replaceState) {
                    history.replaceState(null, "", cfg.urls.editorBase + screenId + "/");
                }
                return true;
            })
            .catch(() => { toast("Sunucuya ulaşılamadı.", "danger"); return false; });
    }

    return {
        get screenId() { return screenId; },
        set screenId(v) { screenId = v; },
        save: save,
        payload: payload,

        /** Kayitli 3B sahneleri listele (Ac diyalogu). */
        list() {
            return fetch(cfg.urls.list + "?kind=3d", {
                credentials: "same-origin",
                headers: { "X-Requested-With": "XMLHttpRequest" },
            }).then((r) => r.json()).then((d) => (d && d.ok ? d.results : []));
        },

        /** Tum mimikler (openMimic hedef dropdown'u — 2B+3B capraz). */
        listAll() {
            return fetch(cfg.urls.list, {
                credentials: "same-origin",
                headers: { "X-Requested-With": "XMLHttpRequest" },
            }).then((r) => r.json()).then((d) => (d && d.ok ? d.results : []));
        },

        open(id) {
            return fetch(cfg.urls.get + "?id=" + encodeURIComponent(id), {
                credentials: "same-origin",
                headers: { "X-Requested-With": "XMLHttpRequest" },
            }).then((r) => r.json()).then((d) => {
                if (!d || !d.ok) { toast((d && d.error) || "Sahne bulunamadı.", "danger"); return null; }
                if (d.screen.kind !== "3d") {
                    toast("Bu kayıt 2B mimik — 2B editörde açın.", "warning");
                    return null;
                }
                screenId = d.screen.id;
                setId(screenId);
                loadDoc(d.screen.data, d.screen);
                if (history.replaceState) {
                    history.replaceState(null, "", cfg.urls.editorBase + screenId + "/");
                }
                return d.screen;
            });
        },

        exportJSON() {
            const p = payload(false);
            delete p.thumbnail;              // dosyayi gereksiz sismesin
            const name = (p.name || "sahne").replace(/[^\w\-]+/g, "_");
            download(name + ".mimic3d.json", JSON.stringify(p, null, 2));
        },

        /**
         * JSON ice aktar. 2B belgesini NET mesajla reddeder — sessizce bos
         * sahne acmak kullaniciyi "dosyam bozuk mu?" ikilemine sokar.
         */
        importJSON(file) {
            return new Promise((resolve) => {
                const fr = new FileReader();
                fr.onload = function () {
                    let obj = null;
                    try { obj = JSON.parse(String(fr.result)); } catch (e) { obj = null; }
                    if (!obj) { toast("Geçersiz JSON dosyası.", "danger"); resolve(false); return; }
                    const doc = obj.data || obj;
                    const looks2d = doc && Array.isArray(doc.objects)
                        && doc.objects.some((o) => o && o.left !== undefined);
                    if (looks2d) {
                        toast("Bu dosya 2B mimik — 2B editörde açın.", "warning");
                        resolve(false);
                        return;
                    }
                    loadDoc(doc, obj);
                    toast("Sahne içe aktarıldı.", "success");
                    resolve(true);
                };
                fr.readAsText(file);
            });
        },

        download: download,
    };
}
