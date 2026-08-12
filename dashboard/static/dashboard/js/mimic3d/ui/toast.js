/** mimic3d/ui/toast.js — 2B editorle ayni gecici bildirim (.mimic-toast). */
export function toast(msg, type, ms) {
    const el = document.createElement("div");
    el.className = "mimic-toast" + (type ? " t-" + type : "");
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), ms || 3600);
    return el;
}
