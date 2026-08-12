/**
 * mimic3d/io/gltf.js — GLB dısa aktarma.
 *
 * 2B editordeki "SVG dışa aktar" slotunun 3B karsiligi. SVG bilincli olarak
 * DUSURULDU: `SVGRenderer` PBR bir sahnede (metalness/roughness/ortam haritasi)
 * kullanilamaz cikti verir. GLB ise sahneyi Blender/SolidWorks/Windows 3D
 * Viewer'da acilabilir hale getirir — saha dokumantasyonu icin gercek fayda.
 *
 * Yalniz `contentRoot` aktarilir: izgara/gizmo (helperRoot) ve gokyuzu (bgRoot)
 * tasarim degil aractir.
 */

import { GLTFExporter } from "three/addons/exporters/GLTFExporter.js";

export function exportGLB(stage, filename, onDone, onError) {
    const exporter = new GLTFExporter();
    exporter.parse(
        stage.contentRoot,
        (result) => {
            const blob = new Blob([result], { type: "model/gltf-binary" });
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = (filename || "sahne") + ".glb";
            a.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            if (onDone) onDone();
        },
        (err) => { if (onError) onError(err); },
        { binary: true, onlyVisible: true },
    );
}
