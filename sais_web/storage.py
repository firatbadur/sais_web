"""Hosgorulu (forgiving) WhiteNoise statik dosya storage'i.

WhiteNoise'in CompressedManifestStaticFilesStorage'i, collectstatic sirasinda bir
JS/CSS dosyasinin referans verdigi baska bir dosyayi (orn. sourceMappingURL ile
isaret edilen `.map`) bulamazsa MissingFileError firlatir. Metronic/Bootstrap
varliklari shippelenmeyen .map dosyalarina referans verdigi icin bu, web
container'inin acilis `collectstatic`'ini cokertir -> gunicorn hic baslamaz ->
Caddy 502. Bu storage, eksik referanslari hata yerine UYARI olarak ele alir
(dosya yine toplanir; yalniz o referans yeniden yazilmaz, ki eksik .map zararsiz).
"""
import logging

from whitenoise.storage import CompressedManifestStaticFilesStorage

logger = logging.getLogger(__name__)


class ForgivingManifestStaticFilesStorage(CompressedManifestStaticFilesStorage):
    # Calisma zamani: manifest'te olmayan dosya icin hata yerine ham yolu dondur.
    manifest_strict = False

    def post_process(self, paths, dry_run=False, **options):
        for name, hashed_name, processed in super().post_process(paths, dry_run, **options):
            if isinstance(processed, Exception):
                # Orn. shippelenmeyen bir .map dosyasina referans. collectstatic'i
                # cokertme; logla ve devam et.
                logger.warning("Statik post-process atlandi: %s (%s)", name, processed)
                processed = False
            yield name, hashed_name, processed
