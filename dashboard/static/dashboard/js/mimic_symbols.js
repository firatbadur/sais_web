/* ===========================================================================
 * mimic_symbols.js — SCADA/HMI mimik sembol kütüphanesi
 * ---------------------------------------------------------------------------
 * window.MIMIC_SYMBOLS: kategorize edilmiş SVG sembol tanımları. Editör bu
 * SVG'leri fabric.loadSVGFromString ile tuvale ekler. Her sembolün
 * `dynKey` ile işaretli alt parçaları animasyon (renk/durum) hedefi olabilir;
 * runtime renk animasyonlarını grubun fill verebilen tüm parçalarına uygular.
 *
 * Renk paleti (endüstriyel HMI):
 *   gövde:   #c2ccd6 / kenar #5e6b7a
 *   metal:   #93a1b0
 *   sıvı:    #2f9bd6
 *   yeşil:   #3fbf6f (çalışıyor/açık)  kırmızı: #e4544c (durdu/kapalı/alarm)
 * ======================================================================== */
(function () {
    "use strict";

    // Kısaltma renkler
    var BODY = "#c2ccd6", EDGE = "#5e6b7a", METAL = "#93a1b0",
        DARK = "#37414d", LIQ = "#2f9bd6", GREEN = "#3fbf6f",
        RED = "#e4544c", AMBER = "#f1b44c", STEEL = "#aeb9c4", WHITE = "#ffffff";

    function S(key, name, w, h, svg) {
        return { key: key, name: name, w: w, h: h, svg: svg.trim() };
    }

    var SYMBOLS = [
        // ----------------------------------------------------------------- //
        // VANALAR
        // ----------------------------------------------------------------- //
        { group: "valves", label: "Vanalar", items: [
            S("valve_gate", "Sürgülü Vana", 100, 70,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 70">' +
              '<rect x="3" y="30" width="14" height="10" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="83" y="30" width="14" height="10" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<polygon points="20,18 50,35 20,52" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<polygon points="80,18 50,35 80,52" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<rect x="44" y="4" width="12" height="18" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="34" y="2" width="32" height="6" rx="3" fill="'+DARK+'"/></svg>'),
            S("valve_ball", "Küresel Vana", 100, 60,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">' +
              '<rect x="2" y="24" width="22" height="12" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="76" y="24" width="22" height="12" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<circle cx="50" cy="30" r="22" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<rect x="44" y="30" width="12" height="22" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<circle cx="50" cy="30" r="10" fill="none" stroke="'+DARK+'" stroke-width="4"/></svg>'),
            S("valve_butterfly", "Kelebek Vana", 90, 60,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 60">' +
              '<circle cx="45" cy="30" r="24" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<ellipse cx="45" cy="30" rx="6" ry="22" fill="'+METAL+'" stroke="'+DARK+'" stroke-width="2"/>' +
              '<line x1="45" y1="6" x2="45" y2="54" stroke="'+DARK+'" stroke-width="3"/>' +
              '<circle cx="45" cy="30" r="4" fill="'+DARK+'"/></svg>'),
            S("valve_control", "Kontrol Vanası", 90, 90,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 90">' +
              '<rect x="2" y="58" width="18" height="12" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="70" y="58" width="18" height="12" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<polygon points="22,52 45,64 22,76" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<polygon points="68,52 45,64 68,76" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<rect x="40" y="34" width="10" height="22" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<path d="M22 34 Q45 6 68 34 Z" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2.5"/>' +
              '<line x1="45" y1="6" x2="45" y2="20" stroke="'+DARK+'" stroke-width="3"/></svg>'),
            S("valve_check", "Çek Valf", 90, 60,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 60">' +
              '<rect x="2" y="24" width="18" height="12" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="70" y="24" width="18" height="12" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<circle cx="45" cy="30" r="22" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5"/>' +
              '<polygon points="34,18 60,30 34,42" fill="'+DARK+'"/>' +
              '<line x1="60" y1="14" x2="60" y2="46" stroke="'+DARK+'" stroke-width="3"/></svg>'),
            S("valve_3way", "3-Yollu Vana", 90, 90,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 90">' +
              '<rect x="2" y="40" width="18" height="12" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="70" y="40" width="18" height="12" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="39" y="70" width="12" height="18" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<polygon points="22,34 45,46 22,58" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<polygon points="68,34 45,46 68,58" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<polygon points="33,46 57,46 45,70" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<rect x="36" y="18" width="18" height="6" rx="3" fill="'+DARK+'"/>' +
              '<line x1="45" y1="24" x2="45" y2="34" stroke="'+DARK+'" stroke-width="3"/></svg>'),
            S("valve_solenoid", "Selenoid Vana", 90, 80,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 80">' +
              '<rect x="2" y="48" width="18" height="12" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="70" y="48" width="18" height="12" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<polygon points="22,42 45,54 22,66" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<polygon points="68,42 45,54 68,66" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<rect x="32" y="10" width="26" height="34" rx="3" fill="'+DARK+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<line x1="38" y1="16" x2="38" y2="38" stroke="'+STEEL+'" stroke-width="2"/>' +
              '<line x1="45" y1="16" x2="45" y2="38" stroke="'+STEEL+'" stroke-width="2"/>' +
              '<line x1="52" y1="16" x2="52" y2="38" stroke="'+STEEL+'" stroke-width="2"/></svg>'),
        ]},

        // ----------------------------------------------------------------- //
        // POMPALAR & MOTORLAR
        // ----------------------------------------------------------------- //
        { group: "pumps", label: "Pompalar & Motorlar", items: [
            S("pump_centrifugal", "Santrifüj Pompa", 100, 90,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 90">' +
              '<rect x="20" y="74" width="60" height="12" rx="2" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<circle cx="50" cy="44" r="30" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3" data-dyn="1"/>' +
              '<circle cx="50" cy="44" r="12" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<path d="M50 44 L50 16 M50 44 L74 58 M50 44 L26 58" stroke="'+EDGE+'" stroke-width="3" fill="none"/>' +
              '<rect x="44" y="6" width="12" height="14" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="78" y="38" width="16" height="12" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/></svg>'),
            S("pump_peristaltic", "Peristaltik Pompa", 90, 90,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 90">' +
              '<rect x="14" y="74" width="62" height="12" rx="2" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<circle cx="45" cy="42" r="32" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3" data-dyn="1"/>' +
              '<circle cx="45" cy="42" r="22" fill="none" stroke="'+DARK+'" stroke-width="3"/>' +
              '<circle cx="45" cy="42" r="6" fill="'+DARK+'"/>' +
              '<circle cx="45" cy="22" r="5" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="1.5"/>' +
              '<circle cx="62" cy="52" r="5" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="1.5"/>' +
              '<circle cx="28" cy="52" r="5" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="1.5"/></svg>'),
            S("pump_submersible", "Dalgıç Pompa", 70, 100,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 70 100">' +
              '<rect x="22" y="6" width="26" height="20" rx="3" fill="'+DARK+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="18" y="26" width="34" height="50" rx="6" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3" data-dyn="1"/>' +
              '<line x1="24" y1="36" x2="46" y2="36" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<line x1="24" y1="46" x2="46" y2="46" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<line x1="24" y1="56" x2="46" y2="56" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<polygon points="22,76 48,76 40,94 30,94" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/></svg>'),
            S("motor", "Elektrik Motoru", 100, 70,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 70">' +
              '<rect x="16" y="56" width="60" height="10" rx="2" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="14" y="14" width="56" height="44" rx="6" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3" data-dyn="1"/>' +
              '<line x1="22" y1="20" x2="22" y2="52" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<line x1="30" y1="20" x2="30" y2="52" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<line x1="38" y1="20" x2="38" y2="52" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<text x="56" y="42" font-size="22" font-family="Arial" font-weight="bold" fill="'+DARK+'" text-anchor="middle">M</text>' +
              '<rect x="70" y="28" width="20" height="16" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/></svg>'),
            S("fan", "Fan / Vantilatör", 90, 90,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 90">' +
              '<circle cx="45" cy="45" r="40" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<g fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="1.5" data-dyn="1">' +
              '<path d="M45 45 Q60 20 70 35 Q55 45 45 45Z"/>' +
              '<path d="M45 45 Q70 60 55 70 Q45 55 45 45Z"/>' +
              '<path d="M45 45 Q30 70 20 55 Q35 45 45 45Z"/>' +
              '<path d="M45 45 Q20 30 35 20 Q45 35 45 45Z"/></g>' +
              '<circle cx="45" cy="45" r="8" fill="'+DARK+'"/></svg>'),
            S("mixer", "Karıştırıcı / Agitatör", 80, 100,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 100">' +
              '<rect x="28" y="4" width="24" height="18" rx="3" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<text x="40" y="17" font-size="12" font-family="Arial" font-weight="bold" fill="'+DARK+'" text-anchor="middle">M</text>' +
              '<line x1="40" y1="22" x2="40" y2="80" stroke="'+METAL+'" stroke-width="4"/>' +
              '<line x1="40" y1="64" x2="20" y2="56" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<line x1="40" y1="64" x2="60" y2="56" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<line x1="40" y1="80" x2="22" y2="90" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<line x1="40" y1="80" x2="58" y2="90" stroke="'+EDGE+'" stroke-width="3"/></svg>'),
            S("blower", "Blower / Körük", 100, 80,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 80">' +
              '<circle cx="44" cy="44" r="32" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3" data-dyn="1"/>' +
              '<path d="M44 44 Q44 14 64 22 Q54 44 44 44Z" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="1.5"/>' +
              '<path d="M44 44 Q70 54 62 70 Q44 56 44 44Z" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="1.5"/>' +
              '<path d="M44 44 Q18 50 24 28 Q44 36 44 44Z" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="1.5"/>' +
              '<circle cx="44" cy="44" r="6" fill="'+DARK+'"/>' +
              '<rect x="74" y="34" width="22" height="20" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/></svg>'),
        ]},

        // ----------------------------------------------------------------- //
        // TANKLAR & KAPLAR
        // ----------------------------------------------------------------- //
        { group: "tanks", label: "Tanklar & Kaplar", items: [
            S("tank_vertical", "Dikey Tank", 90, 120,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 120">' +
              '<path d="M10 24 Q10 6 45 6 Q80 6 80 24 L80 100 Q80 114 45 114 Q10 114 10 100 Z" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<ellipse cx="45" cy="24" rx="35" ry="12" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="11" y="60" width="68" height="52" fill="'+LIQ+'" opacity="0.55" data-level="1"/></svg>'),
            S("tank_horizontal", "Yatay Tank", 130, 70,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 130 70">' +
              '<rect x="20" y="10" width="90" height="50" rx="6" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<ellipse cx="20" cy="35" rx="14" ry="25" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<ellipse cx="110" cy="35" rx="14" ry="25" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<rect x="20" y="38" width="90" height="22" fill="'+LIQ+'" opacity="0.55" data-level="1"/></svg>'),
            S("tank_cone", "Konik Tank", 100, 130,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 130">' +
              '<path d="M14 20 L86 20 L86 86 L50 122 L14 86 Z" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<ellipse cx="50" cy="20" rx="36" ry="11" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<path d="M15 70 L85 70 L50 120 Z" fill="'+LIQ+'" opacity="0.55" data-level="1"/></svg>'),
            S("silo", "Silo", 100, 130,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 130">' +
              '<path d="M20 30 L20 92 L50 116 L80 92 L80 30 Z" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<path d="M20 30 Q50 4 80 30 Z" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5"/>' +
              '<rect x="44" y="116" width="12" height="10" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/></svg>'),
            S("reactor", "Reaktör", 100, 130,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 130">' +
              '<rect x="38" y="2" width="24" height="16" rx="3" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<text x="50" y="14" font-size="11" font-weight="bold" fill="'+DARK+'" text-anchor="middle">M</text>' +
              '<path d="M16 34 Q16 18 50 18 Q84 18 84 34 L84 96 Q84 116 50 116 Q16 116 16 96 Z" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<rect x="17" y="66" width="66" height="48" fill="'+LIQ+'" opacity="0.5" data-level="1"/>' +
              '<line x1="50" y1="18" x2="50" y2="90" stroke="'+METAL+'" stroke-width="3"/>' +
              '<line x1="50" y1="82" x2="34" y2="74" stroke="'+DARK+'" stroke-width="2.5"/>' +
              '<line x1="50" y1="82" x2="66" y2="74" stroke="'+DARK+'" stroke-width="2.5"/></svg>'),
            S("basin", "Havuz / Bazen", 140, 80,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 140 80">' +
              '<path d="M8 10 L132 10 L122 72 L18 72 Z" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<path d="M14 34 L126 34 L122 72 L18 72 Z" fill="'+LIQ+'" opacity="0.5" data-level="1"/></svg>'),
            S("clarifier", "Çökeltme Havuzu", 130, 90,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 130 90">' +
              '<ellipse cx="65" cy="20" rx="58" ry="14" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5"/>' +
              '<path d="M7 20 L7 56 Q7 72 65 72 Q123 72 123 56 L123 20" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2.5"/>' +
              '<ellipse cx="65" cy="38" rx="56" ry="12" fill="'+LIQ+'" opacity="0.45" data-level="1"/>' +
              '<line x1="65" y1="6" x2="65" y2="20" stroke="'+DARK+'" stroke-width="3"/>' +
              '<line x1="20" y1="30" x2="110" y2="30" stroke="'+DARK+'" stroke-width="2.5" data-dyn="1"/></svg>'),
        ]},

        // ----------------------------------------------------------------- //
        // ENSTRÜMANLAR
        // ----------------------------------------------------------------- //
        { group: "instruments", label: "Enstrümanlar", items: [
            S("gauge", "Gösterge (Manometre)", 80, 80,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80">' +
              '<circle cx="40" cy="40" r="34" fill="'+WHITE+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<circle cx="40" cy="40" r="34" fill="none" stroke="'+METAL+'" stroke-width="6" opacity="0.4"/>' +
              '<path d="M14 52 A30 30 0 0 1 66 52" fill="none" stroke="'+DARK+'" stroke-width="2"/>' +
              '<line x1="40" y1="40" x2="58" y2="24" stroke="'+RED+'" stroke-width="3" data-dyn="1"/>' +
              '<circle cx="40" cy="40" r="4" fill="'+DARK+'"/></svg>'),
            S("transmitter", "Transmitter", 70, 70,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 70 70">' +
              '<circle cx="35" cy="35" r="30" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3" data-dyn="1"/>' +
              '<line x1="5" y1="35" x2="65" y2="35" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<text x="35" y="28" font-size="13" font-weight="bold" fill="'+DARK+'" text-anchor="middle">FT</text>' +
              '<text x="35" y="52" font-size="11" fill="'+DARK+'" text-anchor="middle">101</text></svg>'),
            S("flowmeter", "Debimetre", 110, 60,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 110 60">' +
              '<rect x="2" y="22" width="20" height="16" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="88" y="22" width="20" height="16" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="22" y="14" width="66" height="32" rx="4" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<path d="M30 30 L46 30 L52 20 L60 40 L66 30 L80 30" fill="none" stroke="'+LIQ+'" stroke-width="2.5"/></svg>'),
            S("level_sensor", "Seviye Sensörü", 50, 110,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 110">' +
              '<rect x="14" y="2" width="22" height="20" rx="3" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2.5" data-dyn="1"/>' +
              '<text x="25" y="16" font-size="10" font-weight="bold" fill="'+DARK+'" text-anchor="middle">LT</text>' +
              '<line x1="25" y1="22" x2="25" y2="96" stroke="'+METAL+'" stroke-width="4"/>' +
              '<circle cx="25" cy="100" r="8" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2"/></svg>'),
            S("ph_probe", "pH Probu", 50, 110,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 50 110">' +
              '<rect x="12" y="2" width="26" height="26" rx="4" fill="'+DARK+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<text x="25" y="20" font-size="11" font-weight="bold" fill="'+WHITE+'" text-anchor="middle">pH</text>' +
              '<rect x="18" y="28" width="14" height="60" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2" data-dyn="1"/>' +
              '<path d="M18 88 Q25 104 32 88 Z" fill="'+LIQ+'" stroke="'+EDGE+'" stroke-width="2"/></svg>'),
            S("analyzer", "Analizör Paneli", 110, 120,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 110 120">' +
              '<rect x="6" y="6" width="98" height="108" rx="6" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<rect x="16" y="16" width="78" height="36" rx="3" fill="'+DARK+'" data-dyn="1"/>' +
              '<text x="55" y="40" font-size="18" font-weight="bold" fill="'+GREEN+'" font-family="monospace" text-anchor="middle">7.21</text>' +
              '<circle cx="26" cy="70" r="6" fill="'+GREEN+'"/><circle cx="46" cy="70" r="6" fill="'+AMBER+'"/><circle cx="66" cy="70" r="6" fill="'+METAL+'"/>' +
              '<rect x="16" y="86" width="78" height="20" rx="3" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2"/></svg>'),
            S("plc", "PLC / Kontrolör", 90, 110,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 110">' +
              '<rect x="8" y="6" width="74" height="98" rx="4" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<rect x="16" y="14" width="58" height="14" rx="2" fill="'+DARK+'"/>' +
              '<circle cx="22" cy="21" r="3" fill="'+GREEN+'" data-dyn="1"/><circle cx="32" cy="21" r="3" fill="'+AMBER+'"/>' +
              '<g fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="1">' +
              '<rect x="16" y="36" width="14" height="60"/><rect x="38" y="36" width="14" height="60"/><rect x="60" y="36" width="14" height="60"/></g></svg>'),
            S("cabinet", "Saha Kabini", 100, 130,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 130">' +
              '<rect x="10" y="8" width="80" height="114" rx="4" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<rect x="18" y="16" width="64" height="98" rx="2" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<circle cx="73" cy="65" r="3" fill="'+DARK+'"/>' +
              '<rect x="26" y="24" width="48" height="20" fill="'+DARK+'" data-dyn="1"/>' +
              '<circle cx="34" cy="58" r="4" fill="'+GREEN+'"/><circle cx="34" cy="74" r="4" fill="'+RED+'"/></svg>'),
        ]},

        // ----------------------------------------------------------------- //
        // BORU & AKIŞ
        // ----------------------------------------------------------------- //
        { group: "pipes", label: "Boru & Bağlantı", items: [
            S("pipe_h", "Boru (Yatay)", 120, 24,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 24">' +
              '<rect x="0" y="6" width="120" height="12" rx="2" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2" data-dyn="1"/>' +
              '<line x1="0" y1="9" x2="120" y2="9" stroke="'+WHITE+'" stroke-width="1.5" opacity="0.5"/></svg>'),
            S("pipe_v", "Boru (Dikey)", 24, 120,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 120">' +
              '<rect x="6" y="0" width="12" height="120" rx="2" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2" data-dyn="1"/>' +
              '<line x1="9" y1="0" x2="9" y2="120" stroke="'+WHITE+'" stroke-width="1.5" opacity="0.5"/></svg>'),
            S("pipe_elbow", "Dirsek", 60, 60,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 60">' +
              '<path d="M48 6 L48 18 Q48 48 18 48 L6 48 L6 36 Q36 36 36 6 Z" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2" data-dyn="1"/></svg>'),
            S("pipe_tee", "Te (T)", 70, 60,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 70 60">' +
              '<rect x="2" y="6" width="66" height="12" rx="2" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2" data-dyn="1"/>' +
              '<rect x="29" y="6" width="12" height="50" rx="2" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2" data-dyn="1"/></svg>'),
            S("pipe_reducer", "Redüksiyon", 70, 40,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 70 40">' +
              '<polygon points="2,6 30,6 68,15 68,25 30,34 2,34" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2" data-dyn="1"/></svg>'),
            S("flange", "Flanş", 40, 50,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 50">' +
              '<rect x="14" y="4" width="12" height="42" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="4" y="8" width="32" height="8" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="4" y="34" width="32" height="8" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/></svg>'),
        ]},

        // ----------------------------------------------------------------- //
        // PROSES EKİPMANI
        // ----------------------------------------------------------------- //
        { group: "process", label: "Proses Ekipmanı", items: [
            S("filter", "Filtre", 70, 110,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 70 110">' +
              '<rect x="14" y="10" width="42" height="90" rx="8" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<rect x="30" y="2" width="10" height="10" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<g stroke="'+EDGE+'" stroke-width="2" opacity="0.6">' +
              '<line x1="14" y1="30" x2="56" y2="30"/><line x1="14" y1="44" x2="56" y2="44"/>' +
              '<line x1="14" y1="58" x2="56" y2="58"/><line x1="14" y1="72" x2="56" y2="72"/>' +
              '<line x1="14" y1="86" x2="56" y2="86"/></g>' +
              '<rect x="14" y="24" width="42" height="20" fill="'+AMBER+'" opacity="0.35" data-dyn="1"/></svg>'),
            S("heat_exchanger", "Isı Eşanjörü", 120, 70,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 70">' +
              '<rect x="10" y="16" width="100" height="38" rx="19" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="3" data-dyn="1"/>' +
              '<path d="M16 35 H104 M16 28 H104 M16 42 H104" stroke="'+EDGE+'" stroke-width="2" fill="none" opacity="0.6"/>' +
              '<rect x="2" y="22" width="12" height="26" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="106" y="22" width="12" height="26" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/></svg>'),
            S("screw_conveyor", "Helezon Konveyör", 130, 50,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 130 50">' +
              '<rect x="6" y="12" width="118" height="28" rx="6" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="3" data-dyn="1"/>' +
              '<path d="M14 26 q8 -12 16 0 q8 12 16 0 q8 -12 16 0 q8 12 16 0 q8 -12 16 0 q8 12 16 0" fill="none" stroke="'+EDGE+'" stroke-width="2.5"/></svg>'),
            S("compressor", "Kompresör", 100, 90,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 90">' +
              '<rect x="14" y="76" width="72" height="10" rx="2" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="20" y="20" width="60" height="56" rx="6" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3" data-dyn="1"/>' +
              '<circle cx="50" cy="48" r="18" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="2.5"/>' +
              '<circle cx="50" cy="48" r="5" fill="'+DARK+'"/>' +
              '<rect x="40" y="6" width="20" height="16" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/></svg>'),
            S("uv_unit", "UV Dezenfeksiyon", 120, 50,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 50">' +
              '<rect x="6" y="14" width="108" height="22" rx="11" fill="'+STEEL+'" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<rect x="20" y="20" width="80" height="10" rx="5" fill="'+AMBER+'" data-dyn="1"/>' +
              '<rect x="2" y="18" width="8" height="14" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<rect x="110" y="18" width="8" height="14" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/></svg>'),
        ]},

        // ----------------------------------------------------------------- //
        // ELEKTRİK & GÖSTERGE
        // ----------------------------------------------------------------- //
        { group: "electrical", label: "Elektrik & Gösterge", items: [
            S("lamp", "Sinyal Lambası", 60, 60,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 60">' +
              '<circle cx="30" cy="30" r="22" fill="'+GREEN+'" stroke="'+EDGE+'" stroke-width="3" data-dyn="1"/>' +
              '<ellipse cx="24" cy="22" rx="8" ry="5" fill="'+WHITE+'" opacity="0.5"/></svg>'),
            S("beacon", "Alarm Çakar", 70, 80,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 70 80">' +
              '<path d="M18 44 Q18 18 35 18 Q52 18 52 44 Z" fill="'+RED+'" stroke="'+EDGE+'" stroke-width="3" data-dyn="1"/>' +
              '<rect x="14" y="44" width="42" height="10" rx="2" fill="'+DARK+'"/>' +
              '<rect x="22" y="54" width="26" height="18" fill="'+METAL+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<ellipse cx="30" cy="30" rx="5" ry="8" fill="'+WHITE+'" opacity="0.4"/></svg>'),
            S("pushbutton", "Buton", 60, 60,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 60 60">' +
              '<circle cx="30" cy="30" r="26" fill="'+DARK+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<circle cx="30" cy="30" r="18" fill="'+GREEN+'" stroke="'+EDGE+'" stroke-width="2" data-dyn="1"/>' +
              '<ellipse cx="24" cy="24" rx="6" ry="4" fill="'+WHITE+'" opacity="0.4"/></svg>'),
            S("switch", "Anahtar / Şalter", 70, 50,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 70 50">' +
              '<circle cx="12" cy="25" r="5" fill="'+DARK+'"/><circle cx="58" cy="25" r="5" fill="'+DARK+'"/>' +
              '<line x1="12" y1="25" x2="50" y2="10" stroke="'+METAL+'" stroke-width="4" data-dyn="1"/></svg>'),
            S("transformer", "Trafo", 80, 90,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 90">' +
              '<circle cx="32" cy="45" r="26" fill="none" stroke="'+EDGE+'" stroke-width="3"/>' +
              '<circle cx="48" cy="45" r="26" fill="none" stroke="'+EDGE+'" stroke-width="3"/></svg>'),
            S("ups", "UPS / Batarya", 90, 90,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 90">' +
              '<rect x="10" y="10" width="70" height="70" rx="6" fill="'+BODY+'" stroke="'+EDGE+'" stroke-width="3" data-dyn="1"/>' +
              '<rect x="22" y="34" width="46" height="22" rx="2" fill="'+GREEN+'" data-dyn="1"/>' +
              '<rect x="32" y="20" width="8" height="8" fill="'+DARK+'"/><rect x="50" y="20" width="8" height="8" fill="'+DARK+'"/>' +
              '<text x="45" y="72" font-size="12" font-weight="bold" fill="'+DARK+'" text-anchor="middle">UPS</text></svg>'),
            S("value_display", "Değer Göstergesi", 110, 50,
              '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 110 50">' +
              '<rect x="2" y="2" width="106" height="46" rx="5" fill="'+DARK+'" stroke="'+EDGE+'" stroke-width="2"/>' +
              '<text x="55" y="34" font-size="24" font-weight="bold" font-family="monospace" fill="'+GREEN+'" text-anchor="middle">0.00</text></svg>'),
        ]},
    ];

    window.MIMIC_SYMBOLS = SYMBOLS;
})();
