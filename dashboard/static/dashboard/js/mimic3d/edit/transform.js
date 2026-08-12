/**
 * mimic3d/edit/transform.js — TransformControls (tasi/dondur/olcekle gizmo'su).
 *
 * 2B'deki Fabric tutamaklarinin karsiligi. Snap Three.js'in yerlesik
 * `setTranslationSnap/setRotationSnap/setScaleSnap` API'siyle yapilir; ozel
 * matematik yazmak gereksiz risk.
 *
 * COKLU SECIM — SAHNE GRAFIGINE DOKUNULMAZ (onemli):
 * Yaygin cozum, secili nesneleri gecici bir "pivot" nesnesine `attach()` edip
 * gizmo'yu ona baglamaktir. Bu yaklasim ELENDI cunku nesnelerin GERCEK ebeveyni
 * gecici olarak pivot olur ve pivot sahne agacinin (contentRoot) DISINDA yasar:
 *   * durum cubugu nesne sayisi yanlis okur,
 *   * ve daha kotusu `toDoc()` o nesneleri HIC GORMEZ -> coklu secim aktifken
 *     KAYDEDILEN SAHNE SECILI NESNELERI KAYBEDER (sessiz veri kaybi).
 * Bunun yerine gizmo bos bir pivot'a baglanir, suruklemede pivotun **delta
 * transformu** hesaplanip her nesneye dunya uzayinda uygulanir. Hiyerarsi
 * hic degismez.
 */

import * as THREE from "three";
import { TransformControls } from "three/addons/controls/TransformControls.js";

export function makeTransform(stage, selection, loop, opts) {
    const options = opts || {};
    const gizmo = new TransformControls(stage.camera, stage.renderer.domElement);
    gizmo.setSpace("world");
    gizmo.size = 0.9;

    // r169+ TransformControls bir Object3D DEGIL; helper'i sahneye eklenir.
    const helper = gizmo.getHelper ? gizmo.getHelper() : gizmo;
    helper.userData.isHelper = true;
    stage.helperRoot.add(helper);
    gizmo.visible = false;
    helper.visible = false;

    const pivot = new THREE.Object3D();
    pivot.name = "__pivot";
    pivot.userData.isHelper = true;
    stage.helperRoot.add(pivot);

    let multi = [];                 // coklu secimde surulen nesneler
    let pivotStart = new THREE.Matrix4();
    let startWorld = [];            // nesnelerin surukleme baslangici dunya matrisleri
    let parentInv = [];             // ebeveyn dunya matrisi tersleri (baslangicta)
    let snapStep = 0.25;
    let snapOn = true;
    let dragging = false;

    const _delta = new THREE.Matrix4();
    const _tmp = new THREE.Matrix4();

    function applySnap() {
        gizmo.setTranslationSnap(snapOn ? snapStep : null);
        gizmo.setRotationSnap(snapOn ? THREE.MathUtils.degToRad(15) : null);
        gizmo.setScaleSnap(snapOn ? 0.1 : null);
    }
    applySnap();

    /** Atasi da secili olan nesneleri disla (grup + cocugu birlikte secilirse
     *  cocuk iki kez donusurdu). */
    function topLevel(items) {
        return items.filter((o) => {
            let p = o.parent;
            while (p) {
                if (items.indexOf(p) >= 0) return false;
                p = p.parent;
            }
            return true;
        });
    }

    function refresh() {
        const items = selection.list();
        multi = [];
        if (!items.length) {
            gizmo.detach();
            gizmo.visible = false;
            helper.visible = false;
            loop.invalidate();
            return;
        }
        if (items.length === 1) {
            gizmo.attach(items[0]);
        } else {
            const box = new THREE.Box3();
            items.forEach((o) => box.expandByObject(o));
            pivot.position.copy(box.getCenter(new THREE.Vector3()));
            pivot.rotation.set(0, 0, 0);
            pivot.scale.set(1, 1, 1);
            pivot.updateMatrixWorld(true);
            multi = topLevel(items);
            gizmo.attach(pivot);
        }
        gizmo.visible = true;
        helper.visible = true;
        loop.invalidate();
    }

    function captureStart() {
        stage.contentRoot.updateMatrixWorld(true);
        pivot.updateMatrixWorld(true);
        pivotStart.copy(pivot.matrixWorld);
        startWorld = multi.map((o) => o.matrixWorld.clone());
        parentInv = multi.map((o) => (
            o.parent ? new THREE.Matrix4().copy(o.parent.matrixWorld).invert() : new THREE.Matrix4()
        ));
    }

    /** Pivotun baslangictan bu yana delta'sini her nesneye dunya uzayinda uygula. */
    function applyDelta() {
        if (!multi.length) return;
        pivot.updateMatrixWorld(true);
        _delta.copy(pivotStart).invert();
        _delta.premultiply(pivot.matrixWorld);      // delta = P1 * P0^-1
        multi.forEach((o, i) => {
            _tmp.copy(_delta).multiply(startWorld[i]);   // yeni dunya matrisi
            _tmp.premultiply(parentInv[i]);              // ebeveyn yereline cevir
            _tmp.decompose(o.position, o.quaternion, o.scale);
            o.updateMatrixWorld(true);
        });
    }

    gizmo.addEventListener("dragging-changed", (ev) => {
        dragging = ev.value;
        stage.controls.enabled = !ev.value;      // OrbitControls ile cakismayi onle
        if (ev.value) {
            loop.requestContinuous("gizmo");
            captureStart();
            // Surukleme BASLANGICINDA tek snapshot (frame basina degil).
            if (options.onDragStart) options.onDragStart();
        } else {
            loop.releaseContinuous("gizmo");
            // Pivotu secimin yeni merkezine tasi (sonraki surukleme dogru baslasin).
            refresh();
            if (options.onDragEnd) options.onDragEnd();
        }
    });
    gizmo.addEventListener("objectChange", () => {
        applyDelta();
        loop.invalidate();
        if (options.onChange) options.onChange();
    });

    return {
        gizmo: gizmo,
        helper: helper,
        refresh: refresh,
        isDragging() { return dragging; },
        setMode(m) {
            if (["translate", "rotate", "scale"].indexOf(m) < 0) return;
            gizmo.setMode(m);
            loop.invalidate();
            if (options.onModeChange) options.onModeChange(m);
        },
        mode() { return gizmo.mode; },
        toggleSpace() {
            const s = gizmo.space === "world" ? "local" : "world";
            gizmo.setSpace(s);
            loop.invalidate();
            return s;
        },
        space() { return gizmo.space; },
        setSnap(on, step) {
            if (on != null) snapOn = !!on;
            if (step != null) snapStep = Number(step) || 0.25;
            applySnap();
            return { on: snapOn, step: snapStep };
        },
        snap() { return { on: snapOn, step: snapStep }; },
        /**
         * Geriye uyum: eski surumde gizmo nesneleri pivota `attach` ederdi ve
         * yeniden-ebeveynleyen islemler once bunu cozmek zorundaydi. Artik
         * hiyerarsi hic degismedigi icin yapilacak is yok; API korunuyor.
         */
        release() { multi = []; },
        dispose() {
            gizmo.detach();
            gizmo.dispose();
            if (helper.parent) helper.parent.remove(helper);
            if (pivot.parent) pivot.parent.remove(pivot);
        },
    };
}
