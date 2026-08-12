/**
 * mimic3d_runtime.js — 3B mimik animasyon / simulasyon motoru.
 * ---------------------------------------------------------------------------
 * 2B karsiligi `mimic_runtime.js` ile AYNI public API'yi sunar (editor sim
 * paneli ve viewer ayni kodu yazsin) + `needsFrame()`:
 *
 *   Mimic3DRuntime({root, invalidate, camera, controls})
 *     -> { start, stop, isRunning, pressButton, releaseButton,
 *          setTag, setTags, reset, getTags, tagList, needsFrame }
 *
 * SEMANTIK PAYLASILIR: ifade motoru, `autoKind` siniflandirmasi, buton
 * davranisi ve `scada` varsayilanlari `mimic_core.js`'ten gelir -> ayni mimik
 * iki renderer'da AYNI davranir (bkz. mimic_core.js docstring).
 *
 * 2B'de overlay'ler `after:render` ile 2D context'e ciziliyordu; 3B'de karsilik
 * gercek mesh/material surumudur (sembol builder'lari `userData.role` ile
 * isaretler; bkz. mimic3d_symbols.js sozlesmesi).
 *
 * ON-DEMAND RENDER: `needsFrame()` yalniz ZAMAN-BAZLI animasyon varsa true
 * doner (blink/rotate/flow/spin/aeration/carousel/washbar/water-ripple/
 * pathFollow/cameraTour). Yalniz deger-eslemeli bir sahne (colorState/level/
 * text) 4 saniyelik poll basina TEK frame cizer.
 */

import * as THREE from "three";

import {
    autoKind, num, ratioOf, tagListFrom, valueOf,
    pressButton as corePress, releaseButton as coreRelease,
} from "mimic/core";
import { ensureOwnMaterials } from "./mimic3d/core/dispose.js";
import { setLabelText } from "./mimic3d/core/factory.js";

/** Zaman-bazli (surekli frame gerektiren) animasyonlar. */
const TIME_BASED = {
    blink: 1, rotate: 1, rotateAxis: 1, pathFollow: 1, cameraTour: 1,
};
/** `auto` altinda zaman-bazli olan sembol turleri. */
const TIME_BASED_AUTO = {
    spin: 1, flow: 1, aeration: 1, carousel: 1, washbar: 1, blink: 1, water: 1,
};

const DEG = Math.PI / 180;

export function Mimic3DRuntime(opts) {
    const options = opts || {};
    const root = options.root;
    const invalidate = options.invalidate || function () {};
    const camera = options.camera || null;
    const controls = options.controls || null;

    let running = false;
    let raf = null;
    let lastT = 0;
    let tags = {};
    let timeBased = false;
    const snaps = new Map();          // obj -> snapshot

    /* ------------------------------------------------------------------ */
    /* Bagli nesneler                                                      */
    /* ------------------------------------------------------------------ */
    function bound() {
        const out = [];
        if (!root) return out;
        root.traverse((o) => {
            if (o.userData && o.userData.docId && o.scada
                && o.scada.anim && o.scada.anim !== "none") out.push(o);
        });
        return out;
    }

    function kindOf(obj) {
        return obj.scada.anim === "auto" ? autoKind(obj.userData.symbolKey || "") : obj.scada.anim;
    }

    /* ------------------------------------------------------------------ */
    /* Rol cocuklarini bir kez tara                                        */
    /* ------------------------------------------------------------------ */
    function collectRoles(obj) {
        const r = { indicator: [], tintable: [] };
        obj.traverse((n) => {
            const role = n.userData && n.userData.role;
            if (role) {
                if (role === "indicator") r.indicator.push(n);
                else r[role] = n;
            }
            if (n.isMesh && n.userData && n.userData.tintable) r.tintable.push(n);
        });
        return r;
    }

    function materialsOf(n) {
        if (!n || !n.material) return [];
        return Array.isArray(n.material) ? n.material : [n.material];
    }

    /* ------------------------------------------------------------------ */
    /* Snapshot / restore                                                  */
    /* ------------------------------------------------------------------ */
    function snapshot(obj) {
        const anim = obj.scada.anim;
        const kind = kindOf(obj);
        // Material'a DOKUNACAK animasyonlarda paylasilan material'i klonla.
        // Yoksa bir pompayi yesile boyamak AYNI material'i kullanan TUM
        // pompalari boyar -> 3B'ye ozgu, sessiz ve kafa karistirici hata.
        const touchesMaterial = ["colorState", "fillThreshold", "blink", "opacity", "emissive"]
            .indexOf(anim) >= 0 || ["tint", "blink"].indexOf(kind) >= 0;
        if (touchesMaterial) ensureOwnMaterials(obj);

        const roles = collectRoles(obj);
        const mats = [];
        obj.traverse((n) => {
            materialsOf(n).forEach((m) => {
                mats.push({
                    m: m,
                    color: m.color ? m.color.clone() : null,
                    opacity: m.opacity,
                    transparent: m.transparent,
                    emissive: m.emissive ? m.emissive.clone() : null,
                    emissiveIntensity: m.emissiveIntensity,
                });
                // blink/opacity opacity'yi surer; `transparent` acilmazsa
                // three.js opacity'yi SESSIZCE yok sayar.
                if (["blink", "opacity"].indexOf(anim) >= 0 || kind === "blink") m.transparent = true;
            });
        });

        snaps.set(obj, {
            position: obj.position.clone(),
            quaternion: obj.quaternion.clone(),
            scale: obj.scale.clone(),
            visible: obj.visible,
            roles: roles,
            mats: mats,
            rotAccum: 0,
            labelText: obj.userData ? obj.userData.lastText : null,
            liquidScaleY: roles.liquid ? roles.liquid.scale.y : 1,
            liquidY: roles.liquid ? roles.liquid.position.y : 0,
            surfaceY: roles.liquidSurface ? roles.liquidSurface.position.y : 0,
            rotorRot: roles.rotor ? roles.rotor.rotation.clone() : null,
            needleRot: roles.needle ? roles.needle.rotation.clone() : null,
            carouselRot: roles.carousel ? roles.carousel.rotation.clone() : null,
            doorL: roles.doorLeafL ? roles.doorLeafL.position.clone() : null,
            doorR: roles.doorLeafR ? roles.doorLeafR.position.clone() : null,
        });
    }

    function restore(obj) {
        const s = snaps.get(obj);
        if (!s) return;
        obj.position.copy(s.position);
        obj.quaternion.copy(s.quaternion);
        obj.scale.copy(s.scale);
        obj.visible = s.visible;
        s.mats.forEach((rec) => {
            if (rec.color && rec.m.color) rec.m.color.copy(rec.color);
            rec.m.opacity = rec.opacity;
            rec.m.transparent = rec.transparent;
            if (rec.emissive && rec.m.emissive) rec.m.emissive.copy(rec.emissive);
            if (rec.emissiveIntensity != null) rec.m.emissiveIntensity = rec.emissiveIntensity;
            rec.m.needsUpdate = true;
        });
        const r = s.roles;
        if (r.liquid) { r.liquid.scale.y = s.liquidScaleY; r.liquid.position.y = s.liquidY; }
        if (r.liquidSurface) { r.liquidSurface.position.y = s.surfaceY; r.liquidSurface.visible = true; }
        if (r.bubbles) r.bubbles.visible = false;
        if (r.spray) r.spray.visible = false;
        if (r.flow) r.flow.visible = false;
        if (r.rotor && s.rotorRot) r.rotor.rotation.copy(s.rotorRot);
        if (r.needle && s.needleRot) r.needle.rotation.copy(s.needleRot);
        if (r.carousel && s.carouselRot) r.carousel.rotation.copy(s.carouselRot);
        if (r.doorLeafL && s.doorL) r.doorLeafL.position.copy(s.doorL);
        if (r.doorLeafR && s.doorR) r.doorLeafR.position.copy(s.doorR);
        if (s.labelText != null && obj.userData && obj.userData.labelCanvas) {
            setLabelText(obj, s.labelText);
        }
    }

    /* ------------------------------------------------------------------ */
    /* Yardimcilar                                                         */
    /* ------------------------------------------------------------------ */
    function tint(obj, s, color) {
        const r = s.roles;
        // Gosterge (indicator) rolu varsa YALNIZ o boyanir — 2B'deki
        // INDICATOR_TINT davranisinin karsiligi (pano gövdesi boyanmaz).
        const targets = r.indicator.length ? r.indicator : r.tintable;
        targets.forEach((n) => {
            materialsOf(n).forEach((m) => {
                if (m.color) m.color.set(color);
                if (m.emissive) m.emissive.set(color);
            });
        });
    }

    function setEmissive(obj, s, intensity, color) {
        const r = s.roles;
        const targets = r.indicator.length ? r.indicator : r.tintable;
        targets.forEach((n) => {
            materialsOf(n).forEach((m) => {
                if (m.emissive && color) m.emissive.set(color);
                if (m.emissiveIntensity != null) m.emissiveIntensity = intensity;
            });
        });
    }

    function setOpacity(obj, v) {
        obj.traverse((n) => {
            materialsOf(n).forEach((m) => { m.transparent = true; m.opacity = v; });
        });
    }

    /** Sivi seviyesi + yuzey konumu (water/aeration/level). */
    function driveLiquid(obj, s, ratio, now) {
        const r = s.roles;
        if (!r.liquid) return false;
        const u = obj.userData;
        const bottom = typeof u.liqBottom === "number" ? u.liqBottom : 0;
        const innerH = typeof u.liqInnerH === "number" ? u.liqInnerH
            : ((typeof u.liqTop === "number" ? u.liqTop : 1) - bottom);
        const h = Math.max(0.001, innerH * ratio);
        r.liquid.scale.y = Math.max(0.001, ratio);
        r.liquid.position.y = bottom + h / 2;
        if (r.liquidSurface) {
            // Yuzey sivinin ustunde; hafifce salinir (dalga hissi).
            r.liquidSurface.position.y = bottom + h + Math.sin(now / 700) * innerH * 0.004;
            r.liquidSurface.visible = ratio > 0.01;
            // Normal map kaydirma -> kirisim hareketi (2B'deki dalga overlay'i).
            materialsOf(r.liquidSurface).forEach((m) => {
                if (m.normalMap) {
                    m.normalMap.offset.x = (now / 9000) % 1;
                    m.normalMap.offset.y = (now / 14000) % 1;
                }
            });
        }
        return true;
    }

    /** Yukselen kabarciklar. */
    function driveBubbles(s, active, dt, ratio) {
        const p = s.roles.bubbles;
        if (!p) return;
        p.visible = active;
        if (!active) return;
        const attr = p.geometry.getAttribute("position");
        const H = p.userData.bubbleH || 1;
        const speed = 0.35 + ratio * 0.5;
        for (let i = 0; i < attr.count; i++) {
            let y = attr.getY(i) + dt * speed * (0.6 + (p.userData.phase[i] || 0) * 0.8);
            if (y > H) y = 0;
            attr.setY(i, y);
        }
        attr.needsUpdate = true;
    }

    /** Yikama bari spreyi. */
    function driveSpray(s, active, now) {
        const p = s.roles.spray;
        if (!p) return;
        p.visible = active;
        if (!active) return;
        const attr = p.geometry.getAttribute("position");
        const W = p.userData.barW || 1;
        const seed = p.userData.seed;
        const reach = 0.55;
        for (let i = 0; i < attr.count; i++) {
            const sd = seed[i];
            const t = ((now / 900) + sd) % 1;
            const side = (i % 2) ? 1 : -1;
            attr.setX(i, (sd - 0.5) * W * 0.85);
            attr.setY(i, -t * reach * 1.1);
            attr.setZ(i, side * t * reach * (0.5 + sd * 0.6));
        }
        attr.needsUpdate = true;
    }

    /** Boru akisi: kesikli texture offset'i kaydir. */
    function driveFlow(s, active, dt, speed) {
        const f = s.roles.flow;
        if (!f) return;
        f.visible = active;
        if (!active) return;
        materialsOf(f).forEach((m) => {
            if (m.map) m.map.offset.x = (m.map.offset.x - dt * 0.6 * speed) % 1;
        });
    }

    /* ------------------------------------------------------------------ */
    /* Kamera turu (cameraTour)                                            */
    /* ------------------------------------------------------------------ */
    let tour = null;
    function startTour(vps) {
        if (!camera || !controls || !vps || !vps.length) return;
        tour = {
            vps: vps, idx: 0, t0: performance.now(),
            from: { p: camera.position.clone(), t: controls.target.clone() },
            savedCam: camera.position.clone(),
            savedTarget: controls.target.clone(),
        };
    }
    function stopTour() {
        if (tour && camera && controls) {
            camera.position.copy(tour.savedCam);
            controls.target.copy(tour.savedTarget);
            controls.update();
        }
        tour = null;
    }
    function driveTour(now) {
        if (!tour) return;
        const vp = tour.vps[tour.idx % tour.vps.length];
        const dur = Math.max(300, Number(vp.duration) || 2500);
        const k = Math.min(1, (now - tour.t0) / dur);
        const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
        const p1 = new THREE.Vector3().fromArray(vp.position || [10, 8, 12]);
        const t1 = new THREE.Vector3().fromArray(vp.target || [0, 1, 0]);
        camera.position.lerpVectors(tour.from.p, p1, e);
        controls.target.lerpVectors(tour.from.t, t1, e);
        controls.update();
        if (k >= 1) {
            tour.idx += 1;
            tour.t0 = now;
            tour.from = { p: camera.position.clone(), t: controls.target.clone() };
        }
    }

    /* ------------------------------------------------------------------ */
    /* Tek kare                                                            */
    /* ------------------------------------------------------------------ */
    function tick(now) {
        if (!running) return;
        const dt = lastT ? Math.min(0.1, (now - lastT) / 1000) : 0;
        lastT = now;

        bound().forEach((obj) => {
            const sc = obj.scada;
            let s = snaps.get(obj);
            if (!s) { snapshot(obj); s = snaps.get(obj); }

            const val = valueOf(sc, tags);
            const thr = sc.threshold == null ? 1 : Number(sc.threshold);
            const mn = sc.min == null ? 0 : Number(sc.min);
            const mx = sc.max == null ? 100 : Number(sc.max);
            const ratio = ratioOf(val, mn, mx);
            const on = val >= thr;
            const speed = num(sc.speed, 1);
            const axis = ["x", "y", "z"].indexOf(sc.axis) >= 0 ? sc.axis : "y";
            const onColor = sc.onColor || "#3fbf6f";
            const offColor = sc.offColor || "#e4544c";

            switch (sc.anim) {
                /* --- 11 genel animasyon (2B ile ayni semantik) --- */
                case "colorState":
                case "fillThreshold":
                    tint(obj, s, on ? onColor : offColor);
                    break;
                case "blink": {
                    // 2B ile AYNI periyot ve genlik (sin(now/130)).
                    const a = on ? 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(now / 130)) : 1;
                    setOpacity(obj, on ? a : s.mats.length ? s.mats[0].opacity : 1);
                    if (on) setEmissive(obj, s, a * 2, onColor);
                    else setEmissive(obj, s, 0.25, offColor);
                    break;
                }
                case "rotate":
                    // 2B paritesi: speed=1 -> 90°/s. `stop()` quaternion'i
                    // snapshot'tan geri yukledigi icin dogrudan biriktiriyoruz.
                    if (on || (val > 0 && thr <= 0)) {
                        obj.rotation[axis] += dt * (Math.PI / 2) * speed;
                    }
                    break;
                case "level":
                    // Sivi cocugu varsa onu sur, yoksa nesneyi tabandan olcekle.
                    if (!driveLiquid(obj, s, ratio, now)) {
                        obj.scale.y = Math.max(0.001, s.scale.y * ratio);
                    }
                    break;
                case "visibility":
                    obj.visible = on;
                    break;
                case "opacity":
                    setOpacity(obj, ratio);
                    break;
                case "moveX":
                    obj.position.x = s.position.x + ratio * num(sc.moveRange, 1);
                    break;
                case "moveY":
                    obj.position.y = s.position.y + ratio * num(sc.moveRange, 1);
                    break;
                case "text":
                    setLabelText(obj, val.toFixed(sc.decimals == null ? 1 : sc.decimals)
                        + ((sc.showUnit !== false && sc.unit) ? " " + sc.unit : ""));
                    break;

                /* --- 7 yeni 3B animasyonu --- */
                case "moveZ":
                    obj.position.z = s.position.z + ratio * num(sc.moveRange, 1);
                    break;
                case "rotateAxis":
                    if (on || (val > 0 && thr <= 0)) {
                        obj.rotation[axis] += dt * (Math.PI / 2) * speed;
                    }
                    break;
                case "scaleAxis":
                    obj.scale[axis] = Math.max(0.001, s.scale[axis] * ratio);
                    break;
                case "emissive":
                    setEmissive(obj, s, ratio * 2.2, onColor);
                    break;
                case "tilt": {
                    const tmin = num(obj.userData.tiltMin, 0);
                    const tmax = num(obj.userData.tiltMax, 90 * DEG);
                    obj.rotation[axis] = tmin + (tmax - tmin) * ratio;
                    break;
                }
                case "pathFollow": {
                    const path = (options.paths || []).find((p) => p.id === sc.pathId);
                    if (path && path.points && path.points.length > 1) {
                        const pts = path.points.map((p) => new THREE.Vector3().fromArray(p));
                        const curve = new THREE.CatmullRomCurve3(pts, !!path.closed);
                        const p = curve.getPointAt(Math.min(0.999, Math.max(0, ratio)));
                        obj.position.copy(p);
                        if (sc.orient) {
                            const tg = curve.getTangentAt(Math.min(0.999, Math.max(0, ratio)));
                            obj.lookAt(p.clone().add(tg));
                        }
                    }
                    break;
                }
                case "cameraTour":
                    if (on && !tour) startTour(options.viewpoints || []);
                    else if (!on && tour) stopTour();
                    break;

                /* --- sembole ozel (auto): 8 overlay turu + tint/blink --- */
                case "auto": {
                    const kind = autoKind(obj.userData.symbolKey || "");
                    if (kind === "tint") {
                        tint(obj, s, on ? onColor : offColor);
                    } else if (kind === "blink") {
                        const a = on ? 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(now / 130)) : 1;
                        if (on) { setOpacity(obj, a); setEmissive(obj, s, a * 2, onColor); }
                        else { setOpacity(obj, 1); setEmissive(obj, s, 0.25, offColor); }
                    } else if (kind === "water") {
                        driveLiquid(obj, s, ratio, now);
                    } else if (kind === "aeration") {
                        // 2B paritesi: havalandirmada seviye en az 0.4 gosterilir.
                        driveLiquid(obj, s, Math.max(ratio, 0.4), now);
                        driveBubbles(s, on && val > 0, dt, ratio);
                    } else if (kind === "spin") {
                        const r = s.roles.rotor;
                        if (r && on && val > 0) {
                            // 2B paritesi: dt * speed * 3.2
                            const sa = ["x", "y", "z"].indexOf(obj.userData.spinAxis) >= 0
                                ? obj.userData.spinAxis : "z";
                            r.rotation[sa] += dt * speed * 3.2;
                        }
                    } else if (kind === "gauge") {
                        const nd = s.roles.needle;
                        if (nd) {
                            const na = ["x", "y", "z"].indexOf(obj.userData.needleAxis) >= 0
                                ? obj.userData.needleAxis : "z";
                            const a0 = num(obj.userData.needleMin, 210 * DEG);
                            const a1 = num(obj.userData.needleMax, 330 * DEG);
                            nd.rotation[na] = a0 + (a1 - a0) * ratio;
                        }
                    } else if (kind === "flow") {
                        driveFlow(s, val > 0, dt, speed);
                    } else if (kind === "carousel") {
                        const c = s.roles.carousel;
                        if (c && on && val > 0) c.rotation.y += dt * speed * 1.1;
                    } else if (kind === "washbar") {
                        driveSpray(s, on && val > 0, now);
                    } else if (kind === "door") {
                        const travel = num(obj.userData.doorTravel, 0.5);
                        if (s.roles.doorLeafL && s.doorL) {
                            s.roles.doorLeafL.position.x = s.doorL.x - ratio * travel;
                        }
                        if (s.roles.doorLeafR && s.doorR) {
                            s.roles.doorLeafR.position.x = s.doorR.x + ratio * travel;
                        }
                    }
                    break;
                }
            }
        });

        if (tour) driveTour(now);
        invalidate();
        raf = requestAnimationFrame(tick);
    }

    /** Sahnede ZAMAN-BAZLI animasyon var mi? (on-demand render karari) */
    function computeTimeBased() {
        return bound().some((o) => {
            const a = o.scada.anim;
            if (TIME_BASED[a]) return true;
            if (a !== "auto") return false;
            return !!TIME_BASED_AUTO[autoKind(o.userData.symbolKey || "")];
        });
    }

    return {
        start() {
            if (running) return;
            snaps.clear();
            bound().forEach(snapshot);
            timeBased = computeTimeBased();
            running = true;
            lastT = 0;
            raf = requestAnimationFrame(tick);
        },
        stop() {
            running = false;
            if (raf) cancelAnimationFrame(raf);
            raf = null;
            stopTour();
            bound().forEach(restore);
            snaps.clear();
            invalidate();
        },
        isRunning() { return running; },
        pressButton(o) { corePress(o.scada || {}, tags); invalidate(); },
        releaseButton(o) { coreRelease(o.scada || {}, tags); invalidate(); },
        setTag(name, val) { tags[name] = Number(val); invalidate(); },
        setTags(obj) { tags = Object.assign({}, obj); invalidate(); },
        reset() { tags = {}; },
        getTags() { return tags; },
        tagList() { return tagListFrom(bound()); },
        /** Surekli frame gerekiyor mu (render_loop "runtime" jetonu). */
        needsFrame() { return running && timeBased; },
        /** Sahne degistiyse (nesne eklendi/silindi) yeniden hesapla. */
        refresh() {
            if (!running) return;
            bound().forEach((o) => { if (!snaps.has(o)) snapshot(o); });
            timeBased = computeTimeBased();
        },
    };
}

export default Mimic3DRuntime;
