from rest_framework.generics import GenericAPIView
from rest_framework.views import APIView
from rest_framework.mixins import ListModelMixin, CreateModelMixin
from users.models import *
from .serializers import *
from rest_framework import generics,status
from .permissions import *
from rest_framework.filters import SearchFilter
from rest_framework.authtoken.models import Token
from rest_framework.response import Response
from django.db.models import Q
from rest_framework.authentication import SessionAuthentication,BasicAuthentication
from rest_framework.permissions import AllowAny,IsAuthenticated
from rest_framework.decorators import api_view,permission_classes,authentication_classes
from rest_framework.response import Response
import datetime
from .models import *
import pandas as pd
from datetime import datetime
from django.utils.dateparse import parse_datetime
import numpy as np
from .helpers import *

# Sunucu saatini getiren servis
class GetServerDatetimeView(APIView):

    permission_classes = [IsAdminUserOrReadOnly]   # sadece admin erişimi için
    authentication_classes = [BasicAuthentication]

    def get(self, request, format=None):
        station_id = request.GET.get("stationId")
        if not station_id:
            return Response({
                "result": False,
                "message": "stationId parametresi zorunlu.",
                "objects": None
            })

        # İstasyon doğrulama (opsiyonel)
        # from .models import SimInformation
        # if not SimInformation.objects.filter(sim_id=station_id).exists():
        #     return Response({"result": False, "message": "İstasyon bulunamadı.", "objects": None})

        current_datetime = timezone.now().isoformat()
        return Response({
            "result": True,
            "message": None,
            "objects": current_datetime
        })

# İki tarih arası verileri döndüren servis
class GetReadsDataView(generics.ListAPIView):
    serializer_class = ReadsDataSerializer

    def get_queryset(self):
        return Reads.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None
                })

            startDate = parse_datetime(request.GET.get("startDate"))
            endDate   = parse_datetime(request.GET.get("endDate"))
            Period    = int(request.GET.get("Period", 1))

            if not startDate or not endDate:
                return Response({
                    "result": False,
                    "message": "startDate ve endDate zorunlu.",
                    "objects": None
                })

            if endDate <= startDate:
                return Response({
                    "result": False,
                    "message": "endDate > startDate olmalı.",
                    "objects": None
                })

            # stationId filtreleme → parameters üzerinden
            qs = (
                Reads.objects
                .filter(
                    channel__parameters__station__siminformation__sim_id=station_id,
                    time_iso__range=[startDate, endDate]
                )
                .select_related("channel", "channel__parameters", "status")
                .order_by("time_iso")
            )

            if not qs.exists():
                return Response({"result": True, "message": None, "objects": []})

            ser = ReadsDataSerializer(qs, many=True)
            df = pd.DataFrame(ser.data)

            if df.empty:
                return Response({"result": True, "message": None, "objects": []})

            df["ReadTime"] = pd.to_datetime(df["ReadTime"], errors="coerce")
            df = df.dropna(subset=["ReadTime"])
            if df.empty:
                return Response({"result": True, "message": None, "objects": []})

            df["GroupTime"] = df["ReadTime"].dt.floor(f"{Period}min")

            df_pivot = df.pivot_table(
                index="GroupTime",
                columns="ParameterName",
                values="Value",
                aggfunc="mean"
            )

            for pname in df["ParameterName"].unique():
                status_col = (
                    df[df["ParameterName"] == pname]
                    .groupby("GroupTime")["Status"]
                    .last()
                    .rename(f"{pname}_Status")
                )
                df_pivot = df_pivot.join(status_col, how="left")

            df_pivot = df_pivot.reset_index()
            df_pivot = df_pivot.rename(columns={"GroupTime": "ReadTime"})
            df_pivot["Period"] = Period

            response_data = df_to_json_records(df_pivot)

            return Response({"result": True, "message": None, "objects": response_data})

        except Exception as e:
            return Response({"result": False, "message": str(e), "objects": None})

# Anlık verileri döndüren servis
class GetLatestReadsView(generics.ListAPIView):
    serializer_class = ReadsDataSerializer

    def get_queryset(self):
        return Reads.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None
                })

            # 1) İstasyona ait son kayıt zamanını bul
            last_record = (
                Reads.objects
                .filter(channel__parameters__station__siminformation__sim_id=station_id)
                .order_by("-time_iso")
                .first()
            )
            if not last_record:
                return Response({"result": True, "message": None, "objects": []})

            last_time = last_record.time_iso

            # 2) Aynı zamandaki tüm kayıtları çek
            qs = (
                Reads.objects
                .filter(
                    channel__parameters__station__siminformation__sim_id=station_id,
                    time_iso=last_time
                )
                .select_related("channel", "channel__parameters", "status")
            )

            if not qs.exists():
                return Response({"result": True, "message": None, "objects": []})

            # 3) Serialize → DataFrame
            ser = ReadsDataSerializer(qs, many=True)
            df = pd.DataFrame(ser.data)

            if df.empty:
                return Response({"result": True, "message": None, "objects": []})

            # 4) Pivot: parametreler kolon olsun
            df_pivot = df.pivot_table(
                index="ReadTime",  # tek timestamp olacak
                columns="ParameterName",
                values="Value",
                aggfunc="mean"
            )

            # 5) Status kolonlarını ekle
            for pname in df["ParameterName"].unique():
                status_col = (
                    df[df["ParameterName"] == pname]
                    .set_index("ReadTime")["Status"]
                    .rename(f"{pname}_Status")
                )
                df_pivot = df_pivot.join(status_col, how="left")

            df_pivot = df_pivot.reset_index()

            # 6) JSON safe dönüşüm
            response_data = df_to_json_records(df_pivot)

            return Response({"result": True, "message": None, "objects": response_data})

        except Exception as e:
            return Response({"result": False, "message": str(e), "objects": None})

# Son Veri saatini döndüren servis
class GetLastReadTimeView(generics.ListAPIView):

    def get_queryset(self):
        return Reads.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None
                })

            # İlgili istasyona ait son kayıt
            last_record = (
                Reads.objects
                .filter(channel__parameters__station__siminformation__sim_id=station_id)
                .order_by("-time_iso")
                .first()
            )

            if not last_record:
                return Response({"result": True, "message": None, "objects": None})

            return Response({
                "result": True,
                "message": None,
                "objects": last_record.time_iso.isoformat()
            })

        except Exception as e:
            return Response({
                "result": False,
                "message": str(e),
                "objects": None
            })

# Kanal Bilgileri Döndürme Servisi
class GetChannelInfoView(generics.ListAPIView):
    serializer_class = ChannelInfoSerializer

    def get_queryset(self):
        return Sensors.objects.select_related("parameters", "sensorinstants")

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None
                })

            qs = (
                self.get_queryset()
                .filter(parameters__station__siminformation__sim_id=station_id)  # 🔑 Parameters.station_id üzerinden filtreleme
            )

            if not qs.exists():
                return Response({"result": True, "message": None, "objects": []})

            ser = self.get_serializer(qs, many=True)
            return Response({
                "result": True,
                "message": None,
                "objects": ser.data
            })
        except Exception as e:
            return Response({
                "result": False,
                "message": str(e),
                "objects": None
            })

# İstasyon Bilgileri Döndürme Servisi
class GetStationInformationView(generics.ListAPIView):
    serializer_class = StationInfoSerializer

    def get_queryset(self):
        return SimInformation.objects.select_related("station").all()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            if not station_id:
                return Response({"result": False, "message": "stationId parametresi zorunlu.", "objects": None})

            qs = self.get_queryset().filter(sim_id=station_id).first()
            if not qs:
                return Response({"result": False, "message": "İstasyon bulunamadı.", "objects": None})

            ser = self.get_serializer(qs)
            return Response({"result": True, "message": None, "objects": ser.data})

        except Exception as e:
            return Response({"result": False, "message": str(e), "objects": None})

# Kalibrasyon Kayıtlarını Döndüren Servis
class GetCalibrationView(generics.ListAPIView):
    serializer_class = CalibrationResultSerializer

    def get_queryset(self):
        return Calibration.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            startDate = request.GET.get("startDate")
            endDate = request.GET.get("endDate")

            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None
                })

            sim_info = SimInformation.objects.filter(sim_id=station_id).select_related("station").first()
            if not sim_info:
                return Response({
                    "result": False,
                    "message": "Geçersiz stationId.",
                    "objects": None
                })

            start_dt = parse_datetime(startDate) if startDate else None
            end_dt = parse_datetime(endDate) if endDate else None

            if start_dt and end_dt and end_dt <= start_dt:
                return Response({
                    "result": False,
                    "message": "endDate > startDate olmalı.",
                    "objects": None
                })

            qs = Calibration.objects.filter(channel__sensor__station_id=sim_info.station.id)

            if start_dt and end_dt:
                qs = qs.filter(time_iso__range=[start_dt, end_dt])

            qs = qs.select_related("channel", "channel__sensor").order_by("-time_iso")

            ser = self.get_serializer(qs, many=True)
            return Response({"result": True, "message": None, "objects": ser.data})

        except Exception as e:
            return Response({
                "result": False,
                "message": str(e),
                "objects": None
            })

# Açılma Kapanma tarihlerini bildiren servis
class GetPoweroffView(generics.ListAPIView):
    serializer_class = PoweroffResultSerializer

    def get_queryset(self):
        return Poweroff.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None
                })

            # stationId → ilgili Station bul
            sim_info = SimInformation.objects.filter(sim_id=station_id).select_related("station").first()
            if not sim_info:
                return Response({
                    "result": False,
                    "message": "Geçersiz stationId.",
                    "objects": None
                })

            startDate = request.GET.get("startDate")
            endDate = request.GET.get("endDate")

            qs = Poweroff.objects.filter(station=sim_info.station)

            if startDate and endDate:
                start_dt = parse_datetime(startDate)
                end_dt = parse_datetime(endDate)
                if start_dt and end_dt:
                    qs = qs.filter(start_date__gte=start_dt, end_date__lte=end_dt)

            ser = self.get_serializer(qs, many=True)

            return Response({"result": True, "message": None, "objects": ser.data})

        except Exception as e:
            return Response({
                "result": False,
                "message": str(e),
                "objects": None
            })

# Log kayıtları görünütüleme servisi
class GetLogView(generics.ListAPIView):
    serializer_class = LogResultSerializer

    def get_queryset(self):
        return Sys_Log.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            startDate = request.GET.get("startDate")
            endDate = request.GET.get("endDate")

            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None
                })

            # stationId → sim_info üzerinden istasyon bul
            sim_info = SimInformation.objects.filter(sim_id=station_id).select_related("station").first()
            if not sim_info:
                return Response({
                    "result": False,
                    "message": "Geçersiz stationId.",
                    "objects": None
                })

            # Logları istasyona göre filtrele
            qs = Sys_Log.objects.filter(station=sim_info.station)

            # Tarih aralığı uygula
            if startDate and endDate:
                start_dt = parse_datetime(startDate)
                end_dt = parse_datetime(endDate)
                if start_dt and end_dt:
                    qs = qs.filter(time_iso__range=[start_dt, end_dt])

            if not qs.exists():
                return Response({"result": True, "message": None, "objects": []})

            ser = self.get_serializer(qs, many=True)
            return Response({"result": True, "message": None, "objects": ser.data})

        except Exception as e:
            return Response({
                "result": False,
                "message": str(e),
                "objects": None
            })

# Numune almaya başla servisi
class StartSampleView(APIView):
    def get(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            code = request.GET.get("code")

            if not station_id or not code:
                return Response({
                    "result": False,
                    "message": "StationId ve Code parametreleri zorunludur.",
                    "objects": None
                })

            # stationId → SimInformation → Station bul
            sim_info = SimInformation.objects.filter(sim_id=station_id).select_related("station").first()
            if not sim_info:
                return Response({
                    "result": False,
                    "message": "Geçersiz StationId.",
                    "objects": None
                })

            # İstasyona bağlı numune alma sensörünü bul (Parameters.envi_channel=59)
            sensor = Sensors.objects.filter(
                parameters__station_id=sim_info.station.id,
                parameters__envi_channel=59
            ).first()

            if not sensor:
                return Response({
                    "result": False,
                    "message": "İstasyona bağlı numune alma sensörü bulunamadı.",
                    "objects": None
                })

            # Out_Request kaydı oluştur
            Out_Requests.objects.create(
                sensor=sensor,
                value=1,  # Numune alımı başlat (1 = aktif)
                alarm_level=1,  # Bakanlık Talebi
                request_code=code,
                is_completed=False
            )

            # TODO: Burada cihazın gerçekten tetiklenmesi için PLC/RTU’ya komut gönderilebilir.

            return Response({
                "result": True,
                "message": None,
                "objects": True
            })

        except Exception as e:
            return Response({
                "result": False,
                "message": str(e),
                "objects": None
            })


