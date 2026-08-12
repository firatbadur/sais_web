/**
 * mimic3d/core/camera_ops.js — kamera islemleri: sigdir, on-ayar gorunumler,
 * gorus noktasina yumusak ucus.
 *
 * 2B'deki zoomFit/zoom100'un 3B karsiligi. Klasik hata "kamerayi konumlandirip
 * target'i guncellememek"tir; OrbitControls target'i yerinde kaldigi icin
 * sonraki fare hareketinde sahne firlar. Bu yuzden her islem `controls.target`
 * ile birlikte guncellenir.
 */

import * as THREE from "three";

const PRESETS = {
    iso: [1, 0.62, 1.15],
    front: [0, 0.28, 1.6],
    back: [0, 0.28, -1.6],
    left: [-1.6, 0.28, 0],
    right: [1.6, 0.28, 0],
    top: [0.001, 1.9, 0.001],
};

export function makeCameraOps(stage, loop) {
    const cam = stage.camera;
    const controls = stage.controls;

    function frameBox(box, dirVec, padding) {
        if (!box || box.isEmpty()) return;
        const center = box.getCenter(new THREE.Vector3());
        const size = box.getSize(new THREE.Vector3());
        const radius = Math.max(0.4, size.length() * 0.5);
        const fov = (cam.fov * Math.PI) / 180;
        const dist = (radius / Math.sin(fov / 2)) * (padding == null ? 1.25 : padding);
        const dir = (dirVec || new THREE.Vector3(1, 0.62, 1.15)).clone().normalize();
        cam.position.copy(center).add(dir.multiplyScalar(dist));
        controls.target.copy(center);
        // Uzak/yakin sinirlarini sahne olcegine gore genislet ki fit sonrasi
        // tekerlek hemen kilitlenmesin.
        controls.maxDistance = Math.max(controls.maxDistance, dist * 3);
        cam.near = Math.max(0.05, dist / 5000);
        cam.updateProjectionMatrix();
        controls.update();
        loop.invalidate();
    }

    function boundsOf(objs) {
        const box = new THREE.Box3();
        (objs || []).forEach((o) => box.expandByObject(o));
        return box.isEmpty() ? null : box;
    }

    return {
        /** Tum icerigi sigdir; sahne bossa izgara alanini sigdir. */
        fitAll() {
            let box = boundsOf(stage.contentRoot.children);
            if (!box) {
                const s = (stage.sceneSettings.grid && stage.sceneSettings.grid.size) || 40;
                box = new THREE.Box3(
                    new THREE.Vector3(-s / 2, 0, -s / 2),
                    new THREE.Vector3(s / 2, s * 0.15, s / 2));
            }
            frameBox(box, null, 1.3);
        },
        fitObjects(objs) {
            const box = boundsOf(objs);
            if (box) frameBox(box, null, 1.6);
        },
        /** On-ayar gorunum: mevcut hedefi koruyup yonu degistirir. */
        preset(name) {
            const p = PRESETS[name] || PRESETS.iso;
            let box = boundsOf(stage.contentRoot.children);
            if (!box) {
                const s = (stage.sceneSettings.grid && stage.sceneSettings.grid.size) || 40;
                box = new THREE.Box3(
                    new THREE.Vector3(-s / 2, 0, -s / 2),
                    new THREE.Vector3(s / 2, s * 0.15, s / 2));
            }
            frameBox(box, new THREE.Vector3(p[0], p[1], p[2]), 1.3);
        },
        /** Kamerayi bir noktaya odakla (nesne merkezine bak, mesafeyi koru). */
        focusPoint(point) {
            const d = cam.position.distanceTo(controls.target);
            const dir = cam.position.clone().sub(controls.target).normalize();
            controls.target.copy(point);
            cam.position.copy(point).add(dir.multiplyScalar(d));
            controls.update();
            loop.invalidate();
        },
        /**
         * Gorus noktasina yumusak ucus. Sureyi loop'a "tween" jetonuyla
         * bildirir ki on-demand render ucus boyunca frame cizsin.
         */
        flyTo(vp, done) {
            if (!vp) return;
            const p0 = cam.position.clone();
            const t0 = controls.target.clone();
            const p1 = new THREE.Vector3().fromArray(vp.position || [10, 8, 12]);
            const t1 = new THREE.Vector3().fromArray(vp.target || [0, 1, 0]);
            const f0 = cam.fov, f1 = Number(vp.fov) || cam.fov;
            const dur = Math.max(120, Number(vp.duration) || 800);
            const start = performance.now();
            loop.requestContinuous("tween");
            function step(now) {
                const k = Math.min(1, (now - start) / dur);
                // easeInOutCubic
                const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
                cam.position.lerpVectors(p0, p1, e);
                controls.target.lerpVectors(t0, t1, e);
                cam.fov = f0 + (f1 - f0) * e;
                cam.updateProjectionMatrix();
                controls.update();
                loop.invalidate();
                if (k < 1) requestAnimationFrame(step);
                else {
                    loop.releaseContinuous("tween");
                    if (done) done();
                }
            }
            requestAnimationFrame(step);
        },
        /** Mevcut kamerayi gorus noktasi olarak yakala. */
        capture(name) {
            return {
                id: "vp_" + Math.floor(performance.now()).toString(36),
                name: name || "Görüş Noktası",
                position: cam.position.toArray().map((n) => Math.round(n * 1e4) / 1e4),
                target: controls.target.toArray().map((n) => Math.round(n * 1e4) / 1e4),
                fov: cam.fov,
                duration: 800,
            };
        },
    };
}
