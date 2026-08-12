# three.js — vendored (DÜZENLEMEYİN)

3B Mimik Tasarım Stüdyosu'nun render motoru. Bu dizindeki dosyalar **upstream'den
birebir kopyadır**; tek satır bile değiştirilmez (aksi halde sürüm yükseltmesi
sessizce yamayı siler). Bir davranış değişikliği gerekiyorsa kendi kodumuzda
(`dashboard/static/dashboard/js/mimic3d/`) sarmalayın.

| | |
|---|---|
| **Sürüm** | **r185** (npm `three@0.185.0`, 2026-07-01) |
| Kaynak | `https://unpkg.com/three@0.185.0/<yol>` |
| Lisans | MIT — bkz. `LICENSE` |
| Boyut | 18 JS dosyası, ~2.39 MB (WhiteNoise brotli ile telde ~0.5 MB) |

## Dizin yerleşimi — upstream yollarıyla BİREBİR

```
build/three.module.js      <- build/three.module.js        (./three.core.js import eder)
build/three.core.js        <- build/three.core.js
addons/<x>                 <- examples/jsm/<x>
```

`addons/` alt yolları upstream `examples/jsm/` ile **aynı** tutulmalıdır: addon
dosyaları birbirini **göreli** import eder (`./Pass.js`, `../shaders/CopyShader.js`,
`../utils/SkeletonUtils.js`). Yolu değiştirmek bu zinciri kırar.

## Vendor edilen dosyalar ve nedenleri

| Dosya | Neden |
|---|---|
| `build/three.module.js` + `build/three.core.js` | Çekirdek. r150+ ikiye bölündü; **ikisi de** zorunlu |
| `addons/controls/OrbitControls.js` | Kamera gezinme (viewer + editör) |
| `addons/controls/TransformControls.js` | Taşı/döndür/ölçekle gizmo'su (editör) |
| `addons/environments/RoomEnvironment.js` | PMREM ortam haritası — `metalness` fırçalanmış çelik gibi okunsun |
| `addons/exporters/GLTFExporter.js` | "GLB dışa aktar" (2B'deki SVG export'un 3B karşılığı) |
| `addons/loaders/GLTFLoader.js` | GLB/GLTF model içe aktarma (sonraki faz; şimdiden vendor → tek `collectstatic` doğrulaması) |
| `addons/utils/BufferGeometryUtils.js` | `GLTFLoader` göreli bağımlılığı |
| `addons/utils/SkeletonUtils.js` | `GLTFLoader` göreli bağımlılığı — **r185'te eklendi** (r179'da yoktu) |
| `addons/postprocessing/*` (7 dosya) | Seçim vurgusu (`OutlinePass`) — **yalnız editör** yükler, viewer asla |
| `addons/shaders/{CopyShader,OutputShader}.js` | postprocessing göreli bağımlılıkları |

**Bilinçli olarak vendor EDİLMEYENLER:** `DRACOLoader`/`KTX2Loader` (WASM decoder
blob'u gerekir; yalnız sıkıştırılmış GLB için — GLB import fazına bırakıldı),
`three.webgpu.js` (hedef WebGL2; saha PC'lerinde WebGPU güvenilir değil),
`.min` yapıları (aşağı bkz.).

## Neden minify DEĞİL

1. Addon'lar zaten minify değil — karıştırmanın kazancı az.
2. Sahada (NAT arkasında, uzaktan debug edilemeyen kurulumlar) **okunabilir stack
   trace** kritik.
3. WhiteNoise brotli ile ön-sıkıştırıyor: `three.core.js` 1.44 MB → telde ~300 KB;
   sunucu aynı LAN'da.

Yükleme süresi şikâyet olursa `.min` çiftine geçiş **importmap'te iki satırlık**
değişikliktir (`three.module.min.js` + `three.core.min.js` eşleşen çift olarak
yayımlanıyor). Kod değişikliği gerekmez.

## Üretim (DEBUG=0) kısıtı — ESM + ManifestStaticFilesStorage

Bare specifier'lar (`three`, `three/addons/...`) importmap'te `{% static %}` ile
çözülür → hash'lidir. Ama vendor dosyalarının **göreli** import'ları
(`./three.core.js` vb.) iki mekanizmadan biriyle ayakta kalır:

1. `settings.WHITENOISE_KEEP_ONLY_HASHED_FILES = False` → hash'siz kopyalar
   diskte kalır (bu **açıkça** ayarlıdır; `True` yapmak 3B editörü **yalnız
   üretimde** kırar).
2. `sais_web/storage.py` içindeki **dizin-kapsamlı** ESM `patterns` override'ı
   göreli import'ları hash'li adlara yeniden yazar.

İkisi bağımsız katmandır; biri devre dışı kalsa diğeri taşır. Ayrıntı:
`sais_web/storage.py` ve `CLAUDE.md` → "3B Mimik Editörü".

## Yükseltme prosedürü

1. `installer/`/`docs/` gibi değil — burada **dosyaları değiştirmeyin**, komple
   yenisiyle değiştirin (aynı upstream yolları).
2. Göreli import grafiğini yeniden doğrulayın: her `from './x.js'` / `'../y/z.js'`
   hedefi dizinde var mı? (r185'te `SkeletonUtils.js` böyle ortaya çıktı.)
3. Bu dosyadaki sürüm/boyut tablosunu güncelleyin.
4. `DJANGO_DEBUG=0 collectstatic` + tarayıcıda konsol/Network kontrolü
   (bkz. CLAUDE.md P1 çıkış kapısı).
