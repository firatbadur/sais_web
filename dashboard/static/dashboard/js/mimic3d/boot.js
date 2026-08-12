/**
 * mimic3d/boot.js — 3B editorun giris noktasi (ESM).
 *
 * Bu FAZDA (P1) gorevi: ESM/importmap + vendored three.js zincirinin uctan uca
 * calistigini kanitlamak ve P3'un uzerine insa edecegi sahne/dongu/istatistik
 * iskeletini kurmak. Sahne duzenleme araclari (secim, gizmo, outliner, undo,
 * kayit) P3'te eklenir.
 *
 * Sablon `window.M3D_CONFIG` ile besler:
 *   { screenId, name, csrf, urls:{save,get,list,delete,viewer,editorBase,tags},
 *     canControl, initial:{data} }
 */

import * as THREE from "three";

import { makeStage } from "./core/scene.js";
import { makeRenderLoop } from "./core/render_loop.js";
import { migrate, LIMITS } from "./core/doc.js";

const CFG = window.M3D_CONFIG || {};

function $(id) { return document.getElementById(id); }

function toast(msg, type) {
    const el = document.createElement("div");
    el.className = "mimic-toast" + (type ? " t-" + type : "");
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3600);
}

export function boot() {
    // Yetenek kapisi _m3d_shell.html icinde (klasik script) calisti; desteklenmeyen
    // tarayicida hic boot etmeyiz — WebGL context olusturmaya calismak konsolu
    // anlamsiz hatalarla doldurur.
    if (window.M3D_UNSUPPORTED) return null;

    const canvas = $("m3d-canvas");
    if (!canvas) return null;

    const stage = makeStage(canvas, { onResize: () => loop.invalidate() });

    const loop = makeRenderLoop(() => {
        // OrbitControls damping oturana dek surekli frame gerekir.
        const moving = stage.controls.update();
        if (moving) loop.requestContinuous("orbit");
        else loop.releaseContinuous("orbit");
        stage.renderer.render(stage.scene, stage.camera);
    });

    // --- belge ---
    const raw = (CFG.initial && CFG.initial.data) || null;
    const res = migrate(raw);
    if (res.error === "2d-document") {
        toast("Bu kayıt 2B mimik belgesi — 3B editörde açılamaz.", "danger");
    } else {
        stage.applySceneSettings(res.doc.scene);
        stage.applyCameraSettings(res.doc.camera);
        res.warnings.forEach((w) => toast(w, "warning"));
    }

    // --- P1 smoke: bos sahnede yon duygusu veren bir referans nesnesi ---
    // (P3'te gercek nesne yukleyici gelince kaldirilacak.)
    if (!res.doc.objects.length) {
        const g = new THREE.Group();
        g.name = "P1 referans";
        const box = new THREE.Mesh(
            new THREE.BoxGeometry(1.4, 1.4, 1.4),
            new THREE.MeshStandardMaterial({ color: 0x2f9bd6, metalness: 0.25, roughness: 0.45 }));
        box.position.y = 0.7;
        box.castShadow = true;
        const cyl = new THREE.Mesh(
            new THREE.CylinderGeometry(0.55, 0.55, 2.2, 28),
            new THREE.MeshStandardMaterial({ color: 0xaeb9c4, metalness: 0.8, roughness: 0.35 }));
        cyl.position.set(2.6, 1.1, 0);
        cyl.castShadow = true;
        const sph = new THREE.Mesh(
            new THREE.SphereGeometry(0.7, 32, 16),
            new THREE.MeshStandardMaterial({ color: 0x3fbf6f, metalness: 0.1, roughness: 0.4 }));
        sph.position.set(-2.6, 0.7, 0);
        sph.castShadow = true;
        g.add(box, cyl, sph);
        stage.contentRoot.add(g);
        stage.spinDemo = box;
    }

    // --- durum cubugu ---
    const elFps = $("m3d-fps");
    const elGeo = $("m3d-geo");
    const elCount = $("m3d-count");
    const elCalls = $("m3d-calls");
    function updateStats() {
        const info = stage.renderer.info;
        if (elFps) elFps.textContent = String(loop.fps);
        // Sizinti kanaryasi: nesne sayisi sabitken geometri/texture artiyorsa
        // bir yerde dispose atlanmis demektir (bkz. core/dispose.js).
        if (elGeo) elGeo.textContent = info.memory.geometries + " / " + info.memory.textures;
        if (elCalls) elCalls.textContent = String(info.render.calls);
        if (elCount) {
            let n = 0;
            stage.contentRoot.children.forEach(() => { n += 1; });
            elCount.textContent = String(n);
            elCount.classList.toggle("warn", n >= LIMITS.warnObjects);
        }
    }
    setInterval(updateStats, 1000);

    // --- gorunum butonlari ---
    const views = {
        front: [0, 6, 24], back: [0, 6, -24], left: [-24, 6, 0], right: [24, 6, 0],
        top: [0, 28, 0.001], iso: [13.5, 9, 16],
    };
    document.querySelectorAll("[data-view]").forEach((b) => {
        b.addEventListener("click", () => {
            const v = views[b.dataset.view];
            if (!v) return;
            stage.camera.position.set(v[0], v[1], v[2]);
            stage.controls.target.set(0, 1.2, 0);
            stage.controls.update();
            loop.invalidate();
        });
    });
    const btnGrid = $("m3d-toggle-grid");
    if (btnGrid) {
        btnGrid.addEventListener("click", () => {
            stage.grid.visible = !stage.grid.visible;
            btnGrid.classList.toggle("on", stage.grid.visible);
            loop.invalidate();
        });
        btnGrid.classList.toggle("on", stage.grid.visible);
    }
    const btnSpin = $("m3d-toggle-spin");
    if (btnSpin) {
        btnSpin.addEventListener("click", () => {
            const on = !loop.isContinuous("demo");
            if (on) loop.requestContinuous("demo"); else loop.releaseContinuous("demo");
            btnSpin.classList.toggle("on", on);
        });
    }
    // demo donusu render dongusune bagli (surekli jeton tutuldugu surece)
    let last = performance.now();
    const baseRender = loop;
    (function tickDemo() {
        const now = performance.now();
        const dt = Math.min(0.1, (now - last) / 1000);
        last = now;
        if (stage.spinDemo && baseRender.isContinuous("demo")) {
            stage.spinDemo.rotation.y += dt * 0.8;
            stage.spinDemo.rotation.x += dt * 0.25;
        }
        requestAnimationFrame(tickDemo);
    })();

    // --- tema koprusu (chrome temaya uyar, SAHNE UYMAZ) ---
    const btnTheme = $("m3d-theme");
    if (btnTheme) {
        btnTheme.addEventListener("click", () => {
            const cur = document.documentElement.getAttribute("data-bs-theme") === "dark"
                ? "light" : "dark";
            document.documentElement.setAttribute("data-bs-theme", cur);
            try { localStorage.setItem("data-bs-theme", cur); } catch (e) { /* noop */ }
        });
    }

    // --- WebGL context kaybi (entegre GPU'lar dusurur) ---
    canvas.addEventListener("webglcontextlost", (ev) => {
        ev.preventDefault();
        loop.stop();
        toast("Grafik bağlamı kayboldu. Sayfayı yenileyin.", "danger");
    }, false);
    canvas.addEventListener("webglcontextrestored", () => {
        toast("Grafik bağlamı geri geldi.", "success");
        loop.start();
    }, false);

    window.addEventListener("resize", () => stage.resize());
    stage.resize();
    loop.start();

    // Teshis/gelistirme kancasi (P3+ modulleri buradan erisir).
    window.__m3d = { THREE, stage, loop, doc: res.doc, toast };
    return window.__m3d;
}

boot();
