/**
 * mimic3d/edit/ops.js — duzenleme islemleri: hizala/dagit, grupla/coz, yerlestir,
 * cogalt, kopyala/yapistir, sil.
 *
 * 2B karsiliklari ve bilincli sapmalar:
 *  - Hizalama: 2B'nin 6 dugmesi (sol/orta/sag + ust/orta/alt) 3B'de 3 eksen x
 *    3 mod = 9 kombinasyona acilir. **Y'de ust/alt TERSTIR** cunku Y yukaridir.
 *  - Dagitma: merkezleri degil ARDIŞIK BOSLUKLARI esitler; farkli boyutlu
 *    nesnelerde goze dogru gorunen budur (2B'de de sorun cikaran nokta).
 *  - "One getir / arkaya gonder" 3B'de ANLAMSIZ (z-order yok) -> yerine
 *    "Zemine Oturt" ve "Yuzeye Yasla". Olu dugme shipl*enmez*.
 *  - Gruplama `Group.attach()` kullanir (add DEGIL): dunya transformu korunur.
 *    Bu, buradaki en sik atlanan Three.js API'sidir.
 */

import * as THREE from "three";

import { newId } from "../core/doc.js";
import { disposeObject } from "../core/dispose.js";
import { buildFromEntry } from "../core/factory.js";
import { subtreeEntries } from "../core/scene_io.js";

const CLIP_KEY = "mimic3d.clipboard.v1";

function boxOf(o) {
    return new THREE.Box3().setFromObject(o);
}

export function makeOps(stage, selection, opts) {
    const options = opts || {};
    const ctx = options.factoryCtx || {};
    // Coklu secimde gizmo, nesneleri gecici bir pivota `attach` eder. Yeniden
    // ebeveynleme yapan islemler ONCE bu baglantiyi cozmeli; aksi halde
    // nesnelerin gercek ebeveyni pivot olur ve hesaplar/hiyerarsi sasar.
    const releasePivot = options.releasePivot || function () {};

    /** Nesneyi dunya-uzayinda delta kadar kaydir (yerel ebeveyne cevirerek). */
    function moveWorld(obj, deltaWorld) {
        const p = obj.getWorldPosition(new THREE.Vector3()).add(deltaWorld);
        if (obj.parent) obj.parent.worldToLocal(p);
        obj.position.copy(p);
        obj.updateMatrixWorld(true);
    }

    return {
        /* ---------------- hizalama ---------------- */
        /**
         * @param {"x"|"y"|"z"} axis
         * @param {"min"|"center"|"max"} mode
         * Coklu secimde secimin birlesik sinirina, tek nesnede sahne izgarasinin
         * merkezine/sinirina hizalanir (2B'nin "tek nesne sayfaya hizalanir"
         * davranisiyla ayni mantik).
         */
        align(axis, mode) {
            const items = selection.list();
            if (!items.length) return 0;
            let target;
            if (items.length === 1) {
                const s = (stage.sceneSettings.grid && stage.sceneSettings.grid.size) || 40;
                const page = new THREE.Box3(
                    new THREE.Vector3(-s / 2, 0, -s / 2),
                    new THREE.Vector3(s / 2, s, s / 2));
                target = mode === "min" ? page.min[axis]
                    : (mode === "max" ? page.max[axis] : (page.min[axis] + page.max[axis]) / 2);
            } else {
                const u = new THREE.Box3();
                items.forEach((o) => u.expandByObject(o));
                target = mode === "min" ? u.min[axis]
                    : (mode === "max" ? u.max[axis] : (u.min[axis] + u.max[axis]) / 2);
            }
            items.forEach((o) => {
                const b = boxOf(o);
                const cur = mode === "min" ? b.min[axis]
                    : (mode === "max" ? b.max[axis] : (b.min[axis] + b.max[axis]) / 2);
                const d = new THREE.Vector3();
                d[axis] = target - cur;
                moveWorld(o, d);
            });
            return items.length;
        },

        /** Ardisik bosluklari esitle (>=3 nesne gerekir — 2B ile ayni kural). */
        distribute(axis) {
            const items = selection.list();
            if (items.length < 3) return 0;
            const info = items.map((o) => {
                const b = boxOf(o);
                return { o: o, min: b.min[axis], max: b.max[axis], size: b.max[axis] - b.min[axis] };
            }).sort((a, b) => (a.min + a.max) / 2 - (b.min + b.max) / 2);

            const first = info[0], last = info[info.length - 1];
            const span = last.max - first.min;
            const totalSize = info.reduce((s, it) => s + it.size, 0);
            const gap = (span - totalSize) / (info.length - 1);
            let cursor = first.min;
            info.forEach((it) => {
                const d = new THREE.Vector3();
                d[axis] = cursor - it.min;
                moveWorld(it.o, d);
                cursor += it.size + gap;
            });
            return info.length;
        },

        /* ---------------- yerlestirme (2B z-order yerine) ---------------- */
        /** Zemine oturt: alt sinir y=0 olacak sekilde indir/kaldir. */
        dropToGround() {
            const items = selection.list();
            items.forEach((o) => {
                const b = boxOf(o);
                const d = new THREE.Vector3(0, -b.min.y, 0);
                moveWorld(o, d);
            });
            return items.length;
        },

        /** Altindaki en yakin yuzeye yasla (yoksa zemine). */
        snapToSurface() {
            const items = selection.list();
            const ray = new THREE.Raycaster();
            const down = new THREE.Vector3(0, -1, 0);
            items.forEach((o) => {
                const b = boxOf(o);
                const c = b.getCenter(new THREE.Vector3());
                ray.set(new THREE.Vector3(c.x, b.min.y - 0.001, c.z), down);
                // Kendisini ve secimdekileri hedef alma
                const others = stage.contentRoot.children.filter((x) => items.indexOf(x) < 0);
                const hits = ray.intersectObjects(others, true);
                const y = hits.length ? hits[0].point.y : 0;
                moveWorld(o, new THREE.Vector3(0, y - b.min.y, 0));
            });
            return items.length;
        },

        /* ---------------- gruplama ---------------- */
        group() {
            releasePivot();
            const items = selection.list();
            if (items.length < 2) return null;
            const g = new THREE.Group();
            g.userData.docId = newId("g");
            g.userData.docType = "group";
            g.name = "Grup";
            g.scada = { anim: "none" };
            // Pivotu birlesik sinirin merkezine koy ki gizmo ortada dursun.
            const u = new THREE.Box3();
            items.forEach((o) => u.expandByObject(o));
            g.position.copy(u.getCenter(new THREE.Vector3()));
            stage.contentRoot.add(g);
            g.updateMatrixWorld(true);
            // attach(): dunya transformu KORUNUR (add() nesneleri sıçratır).
            items.forEach((o) => g.attach(o));
            selection.set([g]);
            return g;
        },

        ungroup() {
            releasePivot();
            const items = selection.list().filter((o) => o.userData.docType === "group");
            if (!items.length) return 0;
            const freed = [];
            items.forEach((g) => {
                const parent = g.parent || stage.contentRoot;
                g.children.slice().forEach((c) => {
                    if (!c.userData || !c.userData.docId) return;
                    parent.attach(c);
                    freed.push(c);
                });
                disposeObject(g);         // bosalan grubu serbest birak
            });
            selection.set(freed);
            return freed.length;
        },

        /* ---------------- kopyala / yapistir / cogalt ---------------- */
        copy() {
            const items = selection.list();
            if (!items.length) return 0;
            const payload = { schema: "mimic3d/1", objects: subtreeEntries(items) };
            try {
                localStorage.setItem(CLIP_KEY, JSON.stringify(payload));
            } catch (e) { /* private mode: yalniz bellek */ }
            options.memClipboard.value = payload;
            return items.length;
        },

        paste(offset) {
            let payload = null;
            try {
                const raw = localStorage.getItem(CLIP_KEY);
                if (raw) payload = JSON.parse(raw);
            } catch (e) { payload = null; }
            if (!payload) payload = options.memClipboard.value;
            if (!payload || !Array.isArray(payload.objects) || !payload.objects.length) return 0;

            // Id'leri YENIDEN uret (ayni belgeye iki kez yapistirmak cakismasin).
            const idMap = new Map();
            const entries = payload.objects.map((e) => {
                const c = JSON.parse(JSON.stringify(e));
                const nid = newId((c.type || "o")[0]);
                idMap.set(c.id, nid);
                c.id = nid;
                return c;
            });
            entries.forEach((e) => { if (e.parent) e.parent = idMap.get(e.parent) || null; });

            const built = new Map();
            const roots = [];
            entries.forEach((e) => {
                const o = buildFromEntry(e, ctx);
                if (!o) return;
                built.set(e.id, o);
            });
            entries.forEach((e) => {
                const o = built.get(e.id);
                if (!o) return;
                const p = e.parent ? built.get(e.parent) : null;
                if (p) p.add(o);
                else { stage.contentRoot.add(o); roots.push(o); }
            });
            const off = offset == null ? 0.25 : offset;
            roots.forEach((o) => { o.position.x += off; o.position.z += off; });
            selection.set(roots);
            return roots.length;
        },

        duplicate(offset) {
            const items = selection.list();
            if (!items.length) return 0;
            const entries = subtreeEntries(items);
            const built = new Map();
            const roots = [];
            entries.forEach((e) => {
                const o = buildFromEntry(e, ctx);
                if (o) built.set(e.id, o);
            });
            entries.forEach((e) => {
                const o = built.get(e.id);
                if (!o) return;
                const p = e.parent ? built.get(e.parent) : null;
                if (p) p.add(o);
                else { stage.contentRoot.add(o); roots.push(o); }
            });
            const off = offset == null ? 0.25 : offset;
            roots.forEach((o) => { o.position.x += off; o.position.z += off; });
            selection.set(roots);
            return roots.length;
        },

        remove() {
            releasePivot();
            const items = selection.list();
            if (!items.length) return 0;
            selection.clear();
            items.forEach((o) => disposeObject(o));
            return items.length;
        },

        /* ---------------- aynalama ---------------- */
        /**
         * Negatif olcek golge normallerini bozdugundan (ve iki-tarafli render
         * gerektirdiginden) UI'da "Yansit (Y ekseninde 180°)" tercih edilir;
         * yine de gercek ayna gerekirse mirror() sunulur ve DoubleSide'a gecer.
         */
        mirror(axis) {
            const items = selection.list();
            items.forEach((o) => {
                o.scale[axis] *= -1;
                o.traverse((m) => {
                    if (m.isMesh && m.material && !Array.isArray(m.material)) {
                        m.material.side = THREE.DoubleSide;
                    }
                });
            });
            return items.length;
        },

        rotate180(axis) {
            const items = selection.list();
            items.forEach((o) => { o.rotation[axis] += Math.PI; });
            return items.length;
        },

        /** Ok tuslariyla kaydirma (dunya eksenlerinde, snap adimi katlari). */
        nudge(axis, amount) {
            const items = selection.list();
            const d = new THREE.Vector3();
            d[axis] = amount;
            items.forEach((o) => moveWorld(o, d));
            return items.length;
        },
    };
}
