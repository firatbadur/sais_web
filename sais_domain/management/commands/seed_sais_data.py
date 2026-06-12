"""
SAIS'e (atıksu sürekli izleme) özgü seed kayıtlarını oluşturur.

Kullanım:
    python manage.py seed_sais_data

İdempotenttir; birden çok kez çalıştırmak güvenlidir.
"""
from django.core.management.base import BaseCommand

from api.models import Parameter, RequestType, Station, StationType
from sais_domain.models import (
    EnvisoftChannel,
    Scenario,
    ScenarioParameter,
    ScenarioStep,
)


SAIS_STATION_TYPES = [
    ("wastewater_domestic", "Evsel Atıksu", "Yerleşimden kaynaklanan atıksu"),
    ("wastewater_industrial", "Endüstriyel Atıksu", "Sanayi ve ticari faaliyetlerden gelen atıksu"),
    ("wastewater_municipal", "Kentsel Atıksu", "Evsel, endüstriyel ve/veya yağmur suyu karışımı"),
]


SAIS_REQUEST_TYPES = [
    ("ministry_sample", "Bakanlık Numune Talebi"),
    ("auto_scenario", "Otomatik Numune Senaryosu"),
]


# Yerleşik şablon senaryolar — eski sabit-kodlu akışın no-code karşılığı.
# `seed_built_in_scenarios` bunları (varsa) ilk aktif istasyona bağlar ve aktif eder;
# istasyon yoksa şablon olarak (station=None, pasif) bırakır.
BUILTIN_AUTO_PARAMS = ["pH", "KOi", "AKM"]  # izlenen parametreler (gec_min/gec_max eşik)

BUILTIN_AUTO_STEPS = [
    {
        "order": 0, "after_seconds": 0, "label": "1. Limit Aşımı",
        "require_still_exceeded": False,
        "actions": [{"type": "notify", "message": "Numune senaryosu başladı — 1. limit aşımı."}],
    },
    {
        "order": 1, "after_seconds": 300, "label": "2. Alarm",
        "require_still_exceeded": True,
        "actions": [{"type": "notify", "message": "2. alarm — limit aşımı sürüyor."}],
    },
    {
        "order": 2, "after_seconds": 600, "label": "3. Alarm — Numune Al",
        "require_still_exceeded": True,
        "actions": [
            {"type": "ministry_get_code"},
            {"type": "sampler_on"},
            {"type": "send_diagnostic", "type_no": 701, "details": "Numune alınıyor"},
            {"type": "notify", "message": "Numune alınıyor — tetik gönderildi."},
        ],
    },
    {
        "order": 3, "after_seconds": 86400, "label": "Tamamla (24s)",
        "require_still_exceeded": False,
        "actions": [
            {"type": "sim_sample_complete"},
            {"type": "send_diagnostic", "type_no": 702, "details": "Numune alındı"},
            {"type": "sampler_off"},
        ],
    },
]

BUILTIN_MINISTRY_STEPS = [
    {
        "order": 0, "after_seconds": 0, "label": "Numune Al + Bildir",
        "require_still_exceeded": False,
        "actions": [
            {"type": "sampler_on"},
            {"type": "sim_sample_start"},
            {"type": "send_diagnostic", "type_no": 701, "details": "Bakanlık talebi — numune alınıyor"},
            {"type": "notify", "message": "Bakanlık numune talebi — numune alınıyor."},
        ],
    },
    {
        "order": 1, "after_seconds": 3600, "label": "Tamamla (1s)",
        "require_still_exceeded": False,
        "actions": [
            {"type": "sim_sample_complete"},
            {"type": "send_diagnostic", "type_no": 702, "details": "Numune alındı"},
            {"type": "sampler_off"},
        ],
    },
]


# (parameter_name, envi_channel_id) — core seed'de oluşturulan Parameter'lar için
# Envisoft kanal eşlemeleri. parameter_name değeri `api.seed_initial_data` ile
# senkron tutulmalı.
SAIS_ENVI_CHANNEL_MAP = [
    ("pH", 0),
    ("Iletkenlik", 1),
    ("CozunmusOksijen", 2),
    ("Debi", 3),
    ("Sicaklik", 4),
    ("AkisHizi", 5),
    ("KOi", 6),
    ("AKM", 7),
    ("KabinSicaklik", 7),
    ("KabinNem", 8),
    ("GunlukDebi", 9),
    ("GirisDebi", 10),
    ("CikisDebi", 11),
    ("Pompa1", 50),
    ("Pompa2", 51),
    ("Yikama", 52),
    ("HaftalikYikama", 53),
    ("Bakim", 54),
    ("Enerji", 55),
    ("SuBasti", 56),
    ("Duman", 57),
    ("SuYok", 58),
    ("NumuneAlma", 59),
    ("Surucu1", 60),
    ("Surucu2", 61),
    ("Surucu3", 62),
    ("İstasyonBakimda", 63),
    ("Kapi", 64),
    ("AcilStop", 65),
    ("TesisBakimda", 66),
    ("SistemKapali", 67),
    ("Kalibrasyon", 68),
    ("Ups", 69),
    ("ManuelYikama", 70),
    ("SistemDurdurma", 71),
    ("BakimModu", 72),
    ("AcilStopOut", 73),
    ("Numune1Dolu", 74),
    ("Numune2Dolu", 75),
    ("Numune3Dolu", 76),
    ("Numune4Dolu", 77),
    ("Sicaklik", 78),
    ("NumuneReset", 79),
    ("NumuneAnlik", 80),
    ("Surucu1Hata", 81),
    ("Surucu2Hata", 82),
    ("SenaryoSifirla", 83),
    ("Desarj", 84),
]


class Command(BaseCommand):
    help = "SAIS alan-özel seed kayıtlarını oluşturur (idempotent)."

    def handle(self, *args, **options):
        st_created = 0
        for code, name, description in SAIS_STATION_TYPES:
            _, created = StationType.objects.get_or_create(
                code=code, defaults={"name": name, "description": description},
            )
            st_created += int(created)

        rt_created = 0
        for code, name in SAIS_REQUEST_TYPES:
            _, created = RequestType.objects.get_or_create(
                code=code, defaults={"name": name},
            )
            rt_created += int(created)

        env_created = 0
        for parameter_name, envi_id in SAIS_ENVI_CHANNEL_MAP:
            param = Parameter.objects.filter(parameter_name=parameter_name).first()
            if not param:
                continue
            _, created = EnvisoftChannel.objects.get_or_create(
                parameter=param, defaults={"envi_channel_id": envi_id},
            )
            env_created += int(created)

        sc_created = self.seed_built_in_scenarios()

        self.stdout.write(self.style.SUCCESS(
            f"İstasyon Tipleri: {st_created} yeni, "
            f"Talep Tipleri: {rt_created} yeni, "
            f"Envisoft Eşleme: {env_created} yeni, "
            f"Yerleşik Senaryo: {sc_created} yeni."
        ))

    def seed_built_in_scenarios(self):
        """İki yerleşik şablon senaryoyu oluşturur (idempotent, kendi-kendini onarır).

        İlk aktif istasyon varsa senaryolara bağlanır ve aktif edilir; yoksa
        şablon (station=None, pasif) bırakılır. Sonraki çalıştırmada istasyon
        oluşmuşsa station=None şablonlar bağlanıp aktif edilir.
        """
        station = Station.objects.filter(active=True).order_by("id").first()
        created = 0

        # --- Otomatik senaryo ---
        auto, was_created = Scenario.objects.get_or_create(
            name="Varsayılan Otomatik Senaryo", is_builtin=True,
            defaults=dict(
                kind=Scenario.KIND_AUTO, enabled=True,
                avg_window=Scenario.WINDOW_15M, trigger_mode=Scenario.TRIGGER_ANY,
                description="Eşik aşımında 3 kademeli alarm + numune alımı (yerleşik şablon).",
            ),
        )
        created += int(was_created)
        if was_created:
            self._seed_auto_params(auto)
            self._seed_steps(auto, BUILTIN_AUTO_STEPS)

        # --- Bakanlık talepli senaryo ---
        ministry, m_created = Scenario.objects.get_or_create(
            name="Bakanlık Talepli Senaryo", is_builtin=True,
            defaults=dict(
                kind=Scenario.KIND_MINISTRY, enabled=True,
                description="Bakanlık talebinde numune al + Start/Complete bildir (yerleşik şablon).",
            ),
        )
        created += int(m_created)
        if m_created:
            self._seed_steps(ministry, BUILTIN_MINISTRY_STEPS)

        # İstasyon varsa bağla + aktif et (station=None kalmış şablonları onar).
        if station is not None:
            for sc in (auto, ministry):
                if sc.station_id is None:
                    sc.station = station
                    sc.save(update_fields=["station", "updated_at"])
                if not Scenario.objects.filter(
                    station=station, kind=sc.kind, is_active=True,
                ).exists():
                    sc.activate()

        return created

    def _seed_auto_params(self, scenario):
        for name in BUILTIN_AUTO_PARAMS:
            param = Parameter.objects.filter(parameter_name=name).first()
            if not param:
                continue
            ScenarioParameter.objects.get_or_create(
                scenario=scenario, parameter=param,
                defaults=dict(min_value=param.gec_min, max_value=param.gec_max, enabled=True),
            )

    def _seed_steps(self, scenario, steps):
        for s in steps:
            ScenarioStep.objects.get_or_create(
                scenario=scenario, order=s["order"],
                defaults=dict(
                    label=s["label"],
                    after_seconds=s["after_seconds"],
                    require_still_exceeded=s["require_still_exceeded"],
                    actions=s["actions"],
                ),
            )
