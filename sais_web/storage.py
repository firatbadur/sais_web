"""Hosgorulu (forgiving) WhiteNoise statik dosya storage'i.

WhiteNoise'in CompressedManifestStaticFilesStorage'i, collectstatic sirasinda bir
JS/CSS dosyasinin referans verdigi baska bir dosyayi (orn. sourceMappingURL ile
isaret edilen `.map`) bulamazsa MissingFileError firlatir. Metronic/Bootstrap
varliklari shippelenmeyen .map dosyalarina referans verdigi icin bu, web
container'inin acilis `collectstatic`'ini cokertir -> gunicorn hic baslamaz ->
Caddy 502. Bu storage, eksik referanslari hata yerine UYARI olarak ele alir
(dosya yine toplanir; yalniz o referans yeniden yazilmaz, ki eksik .map zararsiz).

Ayrica: 3B mimik editorunun ESM (importmap + three.js) agaci icin **dizin
kapsamli** JS modul import yeniden yazimi ekler -- bkz. ESM_SCOPES.
"""
import logging

from django.contrib.staticfiles.storage import HashedFilesMixin
from whitenoise.storage import CompressedManifestStaticFilesStorage

logger = logging.getLogger(__name__)

#: ESM goreli import'larinin (`./three.core.js`, `../shaders/CopyShader.js`,
#: `./core/scene.js` ...) hash'li adlara yeniden yazilacagi dizinler.
#:
#: NEDEN GLOBAL DEGIL: Django'nun modul-import desenleri DOTALL'dur; bir
#: `import` token'i ile binlerce karakter sonraki `from"./..."` arasini tek
#: eslesme sayabilir. Metronic'in minify bundle'larinda (~17 MB, tek satir) bu
#: calisan bir bundle'i sessizce bozar. Bu yuzden `support_js_module_import_-
#: aggregation = True` (kapsamsiz "*.js") ASLA kullanilmaz; yalnizca kendi
#: yazdigimiz/vendor ettigimiz ESM agaclari kapsanir.
#:
#: GLOB NOTLARI (ikisi de sahada isirdi):
#:  1. `fnmatch`'te `*` bos dizeyle de eslesir ve `/` gecer; bu yuzden desenler
#:     `*/dashboard/...` DEGIL `*dashboard/...` seklindedir -- collectstatic
#:     yollari `dashboard/js/...` gibi onunde ek segment olmadan gelir.
#:  2. `post_process` glob'lari **isletim sistemi ayiricisiyla** gelen yollara
#:     uygular: Linux/Docker'da `dashboard/js/...`, Windows dev'de
#:     `dashboard\js\...`. Tek ayirici yazmak deseni tek OS'ta calisir hale
#:     getirir (Windows'ta sessizce devre disi kalir, uretimde aktif olur ya da
#:     tersi) -> her kapsam IKI varyantla yazilir. Karsi ayiriciyi o platformda
#:     hicbir yol icermedigi icin fazlalik zararsizdir.
_ESM_DIRS = (
    "plugins/custom/three",   # vendored three.js (build/ + addons/)
    "dashboard/js/mimic3d",   # 3B editor modulleri (core/, edit/, io/, ui/)
)
ESM_SCOPES = tuple(
    pattern
    for d in _ESM_DIRS
    for pattern in ("*%s/*.js" % d, "*%s\\*.js" % d.replace("/", "\\"))
)


class ForgivingManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    # Calisma zamani: manifest'te olmayan dosya icin hata yerine ham yolu dondur.
    manifest_strict = False

    # ESM goreli import yeniden yazimi -- yalniz ESM_SCOPES dizinlerinde.
    # `_js_module_import_aggregation_patterns` = (glob, desenler) cifti; glob'unu
    # ("*.js") atip desenlerini kendi dar glob'larimizla eslestiriyoruz.
    patterns = CompressedManifestStaticFilesStorage.patterns + tuple(
        (scope, HashedFilesMixin._js_module_import_aggregation_patterns[1])
        for scope in ESM_SCOPES
    )

    def post_process(self, paths, dry_run=False, **options):
        for name, hashed_name, processed in super().post_process(paths, dry_run, **options):
            if isinstance(processed, Exception):
                # Orn. shippelenmeyen bir .map dosyasina referans. collectstatic'i
                # cokertme; logla ve devam et.
                logger.warning("Statik post-process atlandi: %s (%s)", name, processed)
                processed = False
            yield name, hashed_name, processed
