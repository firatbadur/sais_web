/**
 * mimic3d/core/scene.js — sahne/renderer/kamera kurulumu ve sahne ayarlarinin
 * belge <-> Three.js arasi uygulanmasi.
 *
 * Kok hiyerarsi (ayrim onemli, cunku PNG/thumbnail cekiminde ve secimde farkli
 * davranmalari gerekir):
 *
 *   scene
 *   ├── bgRoot        gradient gokyuzu kubbesi   -> cekimde GORUNUR (arka plan)
 *   ├── groundMesh    zemin duzlemi              -> cekimde GORUNUR (tasarimin parcasi)
 *   ├── helperRoot    izgara + gizmo + secim     -> cekimde GIZLENIR (arac)
 *   └── contentRoot   kullanici nesneleri        -> raycast/secim/animasyon burada
 *
 * TEMA NOTU: Sahne **temaya uymaz**. Arka plan/isik/zemin renkleri kaydedilen
 * tasarimin parcasidir (2B'de tuval arka planinin oldugu gibi) -> ayni mimik
 * acik ve koyu modda, ve PNG'de, ayni gorunur. Yalniz sayfa chrome'u
 * `--mx-*` degiskenleriyle temaya uyar.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";

import { LIMITS, SHADOW_QUALITY, defaultCamera, defaultScene } from "./doc.js";

const TONE_MAPPING = {
    none: THREE.NoToneMapping,
    linear: THREE.LinearToneMapping,
    reinhard: THREE.ReinhardToneMapping,
    cineon: THREE.CineonToneMapping,
    aces: THREE.ACESFilmicToneMapping,
};

/** Dikey gradient gokyuzu — ShaderMaterial'li BackSide kure.
 *
 * Neden mesh: gradient'i CSS ile canvas arkasina koymak daha kolay olurdu ama
 * `renderer.domElement.toDataURL()` yalniz WebGL framebuffer'ini okur -> PNG
 * export ve galeri thumbnail'i arka planı KAYBEDERDI. Kubbe framebuffer'in
 * icinde oldugu icin cekime girer.
 */
function makeSky(radius) {
    const geo = new THREE.SphereGeometry(radius, 32, 16);
    const mat = new THREE.ShaderMaterial({
        side: THREE.BackSide,
        depthWrite: false,
        uniforms: {
            topColor: { value: new THREE.Color("#1c2a3a") },
            bottomColor: { value: new THREE.Color("#080b11") },
            exponent: { value: 1.15 },
        },
        vertexShader: [
            "varying vec3 vWorldPos;",
            "void main() {",
            "  vec4 wp = modelMatrix * vec4(position, 1.0);",
            "  vWorldPos = wp.xyz;",
            "  gl_Position = projectionMatrix * viewMatrix * wp;",
            "}",
        ].join("\n"),
        fragmentShader: [
            "uniform vec3 topColor;",
            "uniform vec3 bottomColor;",
            "uniform float exponent;",
            "varying vec3 vWorldPos;",
            "void main() {",
            // h: -1 (dogrudan asagi) .. +1 (dogrudan yukari). Klasik "pow(max(h,0))"
            // formulu ufuk cizgisinin ALTINI tek renge sabitler ve ufuk hemen
            // uzerini de koyu birakir -> SCADA sahnesinde zemin kenarindan sonra
            // duvar gibi siyahlik gorunur. Tam araligi (0..1) esleyip ufku ara
            // tona getiriyoruz: gokyuzu yukari dogru acilir, zemin altina dogru koyulasir.
            "  float h = normalize(vWorldPos).y;",
            "  float t = pow(clamp(h * 0.5 + 0.5, 0.0, 1.0), exponent);",
            "  gl_FragColor = vec4(mix(bottomColor, topColor, t), 1.0);",
            // ZORUNLU (aksi halde gokyuzu HER ZAMAN neredeyse siyah cikar):
            // three.js r152+ renk yonetimi acik -> `new THREE.Color("#1c2a3a")`
            // uniform'a **linear-srgb** deger yazar. Ozel ShaderMaterial bu degeri
            // dogrudan gl_FragColor'a yazarsa cikis sRGB kodlanmadigi icin deger
            // ~4 kat karanlik gorunur (olculdu: beklenen (28,42,58) yerine (2,3,6);
            // dahasi iki farkli gradient formulu 8-bit'te ayni piksele yuvarlandigi
            // icin "shader degisikligi hicbir sey yapmiyor" gibi gorunur).
            // Asagidaki chunk'lar sahnenin geri kalaniyla ayni tone-mapping +
            // renk-uzayi hattini uygular.
            "#include <tonemapping_fragment>",
            "#include <colorspace_fragment>",
            "}",
        ].join("\n"),
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.name = "__sky";
    mesh.frustumCulled = false;
    mesh.userData.isHelper = true;   // secilemez/export edilemez
    return mesh;
}

/**
 * Sahneyi kur.
 *
 * @param {HTMLCanvasElement} canvas
 * @param {object} [opts] {onResize}
 * @returns {object} stage
 */
export function makeStage(canvas, opts) {
    const options = opts || {};

    const renderer = new THREE.WebGLRenderer({
        canvas,
        antialias: true,
        alpha: false,
        // preserveDrawingBuffer: false (varsayilan, performans icin) -> PNG
        // cekiminde render + toDataURL AYNI TICK'te yapilmali (bkz. io/screenshot.js).
        preserveDrawingBuffer: false,
        powerPreference: "high-performance",
    });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, LIMITS.maxPixelRatio));
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;

    const scene = new THREE.Scene();

    const bgRoot = new THREE.Group();
    bgRoot.name = "__bg";
    const helperRoot = new THREE.Group();
    helperRoot.name = "__helpers";
    const contentRoot = new THREE.Group();
    contentRoot.name = "__content";
    scene.add(bgRoot, helperRoot, contentRoot);

    const camDef = defaultCamera();
    const camera = new THREE.PerspectiveCamera(camDef.fov, 1, camDef.near, camDef.far);
    camera.position.fromArray(camDef.position);

    const sky = makeSky(camDef.far * 0.92);
    bgRoot.add(sky);

    // --- isiklar ---
    const ambient = new THREE.AmbientLight(0xffffff, 0.4);
    const hemi = new THREE.HemisphereLight(0xdfe8f5, 0x3a3f48, 0.35);
    const dir = new THREE.DirectionalLight(0xffffff, 1.15);
    dir.position.set(12, 18, 9);
    dir.castShadow = true;
    dir.shadow.camera.near = 0.5;
    dir.shadow.camera.far = 120;
    scene.add(ambient, hemi, dir, dir.target);

    // --- zemin (tasarimin parcasi -> helperRoot'ta DEGIL) ---
    const groundGeo = new THREE.PlaneGeometry(1, 1);
    const groundMat = new THREE.MeshStandardMaterial({
        color: 0x20242d, roughness: 0.92, metalness: 0.0,
    });
    const groundMesh = new THREE.Mesh(groundGeo, groundMat);
    groundMesh.name = "__ground";
    groundMesh.rotation.x = -Math.PI / 2;
    groundMesh.position.y = -0.001;   // z-fighting: izgara tam y=0'da
    groundMesh.receiveShadow = true;
    groundMesh.userData.isHelper = true;
    scene.add(groundMesh);

    // --- izgara (arac -> cekimde gizlenir) ---
    let grid = new THREE.GridHelper(40, 40, 0x3a4250, 0x252b36);
    grid.name = "__grid";
    grid.userData.isHelper = true;
    helperRoot.add(grid);

    // --- kamera kontrolu ---
    const controls = new OrbitControls(camera, canvas);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.screenSpacePanning = true;
    controls.target.fromArray(camDef.target);
    controls.minDistance = camDef.limits.minDistance;
    controls.maxDistance = camDef.limits.maxDistance;
    controls.maxPolarAngle = camDef.limits.maxPolarAngle;
    controls.update();

    // --- ortam haritasi (PMREM) ---
    // `metalness` degerlerinin firca celigi gibi okunmasini SAGLAYAN sey budur;
    // yoksa metaller siyah cikar. Bir kez uretilir, generator hemen dispose.
    let envTexture = null;
    function buildEnvironment(preset, intensity) {
        if (envTexture) { envTexture.dispose(); envTexture = null; }
        if (preset === "none") {
            scene.environment = null;
            scene.environmentIntensity = 1;
            return;
        }
        const pmrem = new THREE.PMREMGenerator(renderer);
        const room = new RoomEnvironment();
        envTexture = pmrem.fromScene(room, 0.04).texture;
        room.dispose();
        pmrem.dispose();
        scene.environment = envTexture;
        scene.environmentIntensity = intensity == null ? 0.55 : intensity;
    }

    const stage = {
        THREE,
        renderer, scene, camera, controls,
        bgRoot, helperRoot, contentRoot,
        groundMesh, sky,
        lights: { ambient, hemi, dir },
        get grid() { return grid; },
        sceneSettings: defaultScene(),
        cameraSettings: camDef,
    };

    // --- sahne ayarlarini uygula ---
    stage.applySceneSettings = function (s) {
        const st = Object.assign(defaultScene(), s || {});
        stage.sceneSettings = st;

        // arka plan
        if (st.background && st.background.mode === "gradient") {
            sky.visible = true;
            sky.material.uniforms.topColor.value.set(st.background.top || "#1c2a3a");
            sky.material.uniforms.bottomColor.value.set(st.background.bottom || "#080b11");
            scene.background = null;
        } else {
            sky.visible = false;
            scene.background = new THREE.Color((st.background && st.background.color) || "#0f1420");
        }

        // sis
        if (st.fog && st.fog.enabled) {
            scene.fog = new THREE.Fog(st.fog.color || "#0f1420",
                                      Number(st.fog.near) || 20, Number(st.fog.far) || 160);
        } else {
            scene.fog = null;
        }

        // Izgara: GridHelper boyut/bolme/renkleri geometriye (vertex color) gomer,
        // sonradan degistirilemez -> herhangi biri degisince YENIDEN KURULUR.
        // Imza karsilastirmasi gereksiz yeniden kurmayi (ve dispose/alloc
        // trafigini) onler; her sahne-ayari kaydinda cagriliyor.
        const g = st.grid || {};
        const size = Math.max(1, Number(g.size) || 40);
        const div = Math.max(1, Math.round(Number(g.divisions) || 40));
        const c1 = g.color1 || "#3a4250";
        const c2 = g.color2 || "#252b36";
        const gridKey = [size, div, c1, c2].join("|");
        if (grid.userData._key !== gridKey) {
            helperRoot.remove(grid);
            grid.geometry.dispose();
            grid.material.dispose();
            grid = new THREE.GridHelper(size, div, c1, c2);
            grid.name = "__grid";
            grid.userData.isHelper = true;
            grid.userData._key = gridKey;
            helperRoot.add(grid);
        }
        grid.visible = g.visible !== false;

        // zemin
        const gr = st.ground || {};
        groundMesh.visible = gr.visible !== false;
        const gs = Math.max(1, Number(gr.size) || size);
        groundMesh.scale.set(gs, gs, 1);
        groundMat.color.set(gr.color || "#20242d");
        groundMat.roughness = gr.roughness == null ? 0.92 : Number(gr.roughness);
        groundMat.metalness = gr.metalness == null ? 0 : Number(gr.metalness);
        groundMesh.receiveShadow = gr.receiveShadow !== false;

        // isiklar
        const L = st.lights || {};
        if (L.ambient) {
            ambient.color.set(L.ambient.color || "#ffffff");
            ambient.intensity = Number(L.ambient.intensity) || 0;
        }
        if (L.hemi) {
            hemi.color.set(L.hemi.skyColor || "#dfe8f5");
            hemi.groundColor.set(L.hemi.groundColor || "#3a3f48");
            hemi.intensity = Number(L.hemi.intensity) || 0;
        }
        if (L.dir) {
            dir.color.set(L.dir.color || "#ffffff");
            dir.intensity = Number(L.dir.intensity) || 0;
            if (Array.isArray(L.dir.position)) dir.position.fromArray(L.dir.position);
            if (Array.isArray(L.dir.target)) dir.target.position.fromArray(L.dir.target);
            dir.target.updateMatrixWorld();
        }

        // golgeler — golge kamerasi izgara alanini kapsayacak sekilde acilir
        const sh = st.shadows || {};
        const mapSize = sh.enabled === false ? 0 : (SHADOW_QUALITY[sh.quality] || 1024);
        renderer.shadowMap.enabled = mapSize > 0;
        dir.castShadow = mapSize > 0 && (!L.dir || L.dir.castShadow !== false);
        if (mapSize > 0) {
            if (dir.shadow.mapSize.width !== mapSize) {
                dir.shadow.mapSize.set(mapSize, mapSize);
                if (dir.shadow.map) { dir.shadow.map.dispose(); dir.shadow.map = null; }
            }
            const half = size * 0.6;
            dir.shadow.camera.left = -half;
            dir.shadow.camera.right = half;
            dir.shadow.camera.top = half;
            dir.shadow.camera.bottom = -half;
            dir.shadow.bias = sh.bias == null ? -0.0005 : Number(sh.bias);
            dir.shadow.camera.updateProjectionMatrix();
        }

        // tone mapping / pozlama
        renderer.toneMapping = TONE_MAPPING[st.toneMapping] == null
            ? THREE.ACESFilmicToneMapping : TONE_MAPPING[st.toneMapping];
        renderer.toneMappingExposure = st.exposure == null ? 1 : Number(st.exposure);

        // ortam
        const env = st.environment || {};
        buildEnvironment(env.preset || "room", env.intensity);
    };

    stage.readSceneSettings = function () {
        return JSON.parse(JSON.stringify(stage.sceneSettings));
    };

    stage.applyCameraSettings = function (c) {
        const cs = Object.assign(defaultCamera(), c || {});
        stage.cameraSettings = cs;
        camera.fov = Number(cs.fov) || 50;
        camera.near = Number(cs.near) || 0.1;
        camera.far = Number(cs.far) || 400;
        if (Array.isArray(cs.position)) camera.position.fromArray(cs.position);
        if (Array.isArray(cs.target)) controls.target.fromArray(cs.target);
        const lim = cs.limits || {};
        controls.minDistance = lim.minDistance == null ? 1.5 : Number(lim.minDistance);
        controls.maxDistance = lim.maxDistance == null ? 180 : Number(lim.maxDistance);
        controls.maxPolarAngle = lim.maxPolarAngle == null ? 1.5533 : Number(lim.maxPolarAngle);
        camera.updateProjectionMatrix();
        controls.update();
        // gokyuzu kubbesi kamera far'ina gore olceklenir (kamera kubbenin
        // DISINA cikamaz: maxDistance << radius).
        sky.scale.setScalar((camera.far * 0.92) / (camDef.far * 0.92));
    };

    stage.readCameraSettings = function () {
        const cs = stage.cameraSettings;
        return {
            type: "perspective",
            fov: camera.fov, near: camera.near, far: camera.far,
            position: camera.position.toArray(),
            target: controls.target.toArray(),
            orthoZoom: cs.orthoZoom || 40,
            limits: {
                minDistance: controls.minDistance,
                maxDistance: controls.maxDistance,
                maxPolarAngle: controls.maxPolarAngle,
            },
        };
    };

    /** Canvas'i kapsayicisina uydur. */
    stage.resize = function () {
        const parent = canvas.parentElement;
        if (!parent) return;
        const w = Math.max(1, parent.clientWidth);
        const h = Math.max(1, parent.clientHeight);
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        if (options.onResize) options.onResize(w, h);
    };

    stage.dispose = function () {
        controls.dispose();
        if (envTexture) { envTexture.dispose(); envTexture = null; }
        sky.geometry.dispose();
        sky.material.dispose();
        groundGeo.dispose();
        groundMat.dispose();
        grid.geometry.dispose();
        grid.material.dispose();
        if (dir.shadow.map) dir.shadow.map.dispose();
        renderer.dispose();
    };

    stage.applySceneSettings(stage.sceneSettings);
    stage.applyCameraSettings(camDef);
    return stage;
}
