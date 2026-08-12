/**
 * mimic3d/core/selection.js — raycast secimi + OutlinePass vurgusu.
 *
 * KURALLAR
 *  - Isin bir IC MESH'e carpsa bile secilen sey `userData.docId` tasiyan EN
 *    YAKIN ATA'dir. Aksi halde kullanici bir pompanin rotorunu secip pompayi
 *    tasiyamaz hale gelirdi.
 *  - `helperRoot` (izgara/gizmo) ve `bgRoot` (gokyuzu) picking'e girmez.
 *  - `userData.locked` nesneler ne secilir ne gizmo kabul eder.
 *  - Outline `EffectComposer` ile yapilir ve YALNIZ EDITORDE yuklenir; viewer
 *    postprocessing import etmez (mutevazi saha PC'sinde bedava performans).
 */

import * as THREE from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { OutlinePass } from "three/addons/postprocessing/OutlinePass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";

export function makeSelection(stage, opts) {
    const options = opts || {};
    const raycaster = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    let items = [];               // secili Object3D listesi
    let composer = null;
    let outline = null;

    function buildComposer() {
        const size = stage.renderer.getSize(new THREE.Vector2());
        composer = new EffectComposer(stage.renderer);
        composer.addPass(new RenderPass(stage.scene, stage.camera));
        outline = new OutlinePass(size, stage.scene, stage.camera);
        outline.edgeStrength = 4.0;
        outline.edgeGlow = 0.25;
        outline.edgeThickness = 1.4;
        outline.pulsePeriod = 0;
        outline.visibleEdgeColor.set("#009ef7");
        outline.hiddenEdgeColor.set("#0a4f74");
        composer.addPass(outline);
        // OutputPass: tone mapping + renk uzayi donusumunu zincirin SONUNDA
        // uygular; olmazsa composer cikisi ana render'dan farkli (soluk) olur.
        composer.addPass(new OutputPass());
    }
    buildComposer();

    function setSize(w, h) {
        if (composer) composer.setSize(w, h);
        if (outline) outline.setSize(w, h);
    }

    /** docId tasiyan en yakin ata (yok ise null). */
    function docAncestor(o) {
        let n = o;
        while (n) {
            if (n.userData && n.userData.docId) return n;
            n = n.parent;
        }
        return null;
    }

    /** Ekran noktasindan (client koordinati) nesne bul. */
    function pick(clientX, clientY) {
        const el = stage.renderer.domElement;
        const rect = el.getBoundingClientRect();
        ndc.x = ((clientX - rect.left) / rect.width) * 2 - 1;
        ndc.y = -((clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(ndc, stage.camera);
        const hits = raycaster.intersectObjects(stage.contentRoot.children, true);
        for (let i = 0; i < hits.length; i++) {
            const target = docAncestor(hits[i].object);
            if (target && !target.userData.locked && target.visible !== false) {
                return { object: target, point: hits[i].point, distance: hits[i].distance };
            }
        }
        return null;
    }

    /** Zemin duzlemine (y=0) isin dusur — nesne birakma noktasi icin. */
    const groundPlane = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0);
    function pickGround(clientX, clientY) {
        const el = stage.renderer.domElement;
        const rect = el.getBoundingClientRect();
        ndc.x = ((clientX - rect.left) / rect.width) * 2 - 1;
        ndc.y = -((clientY - rect.top) / rect.height) * 2 + 1;
        raycaster.setFromCamera(ndc, stage.camera);
        const p = new THREE.Vector3();
        return raycaster.ray.intersectPlane(groundPlane, p) ? p : null;
    }

    function sync() {
        if (outline) outline.selectedObjects = items.slice();
        if (options.onChange) options.onChange(items.slice());
    }

    return {
        get composer() { return composer; },
        get outline() { return outline; },
        setSize: setSize,
        pick: pick,
        pickGround: pickGround,
        docAncestor: docAncestor,

        list() { return items.slice(); },
        first() { return items[0] || null; },
        count() { return items.length; },
        has(o) { return items.indexOf(o) >= 0; },

        set(objs) {
            items = (objs || []).filter(Boolean).filter((o) => !o.userData.locked);
            sync();
        },
        add(o) {
            if (o && !o.userData.locked && items.indexOf(o) < 0) { items.push(o); sync(); }
        },
        toggle(o) {
            if (!o || o.userData.locked) return;
            const i = items.indexOf(o);
            if (i >= 0) items.splice(i, 1); else items.push(o);
            sync();
        },
        remove(o) {
            const i = items.indexOf(o);
            if (i >= 0) { items.splice(i, 1); sync(); }
        },
        clear() { if (items.length) { items = []; sync(); } },

        /** Sahneden silinen/yeniden kurulan nesneleri secimden dusur. */
        prune(validSet) {
            const before = items.length;
            items = items.filter((o) => (validSet ? validSet.has(o) : !!o.parent));
            if (items.length !== before) sync();
        },

        /** Secimin dunya sinir kutusu (hizalama/fit icin). */
        bounds() {
            if (!items.length) return null;
            const box = new THREE.Box3();
            items.forEach((o) => box.expandByObject(o));
            return box.isEmpty() ? null : box;
        },

        dispose() {
            if (outline) {
                if (outline.dispose) outline.dispose();
                outline = null;
            }
            if (composer) {
                // EffectComposer'in kendi render target'lari GPU'da yer tutar.
                if (composer.renderTarget1) composer.renderTarget1.dispose();
                if (composer.renderTarget2) composer.renderTarget2.dispose();
                composer = null;
            }
        },
    };
}
