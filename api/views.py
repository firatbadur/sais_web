from datetime import datetime

import pandas as pd
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import generics
from rest_framework.authentication import BasicAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from sais_domain.models import SaisCabinet

from .helpers import df_to_json_records
from .models import (
    Calibration,
    OutputRequest,
    PowerOff,
    Reading,
    RequestType,
    Sensor,
    SystemLog,
)
from .permissions import IsAdminUserOrReadOnly
from .serializers import (
    CalibrationResultSerializer,
    ChannelInfoSerializer,
    LogResultSerializer,
    PoweroffResultSerializer,
    ReadsDataSerializer,
    StationInfoSerializer,
)


# Sunucu saatini getiren servis
class GetServerDatetimeView(APIView):

    permission_classes = [IsAdminUserOrReadOnly]
    authentication_classes = [BasicAuthentication]

    def get(self, request, format=None):
        station_id = request.GET.get("stationId")
        if not station_id:
            return Response({
                "result": False,
                "message": "stationId parametresi zorunlu.",
                "objects": None,
            })

        current_datetime = timezone.now().isoformat()
        return Response({
            "result": True,
            "message": None,
            "objects": current_datetime,
        })


# İki tarih arası verileri döndüren servis
class GetReadsDataView(generics.ListAPIView):
    serializer_class = ReadsDataSerializer

    def get_queryset(self):
        return Reading.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None,
                })

            startDate = parse_datetime(request.GET.get("startDate"))
            endDate = parse_datetime(request.GET.get("endDate"))
            Period = int(request.GET.get("Period", 1))

            if not startDate or not endDate:
                return Response({
                    "result": False,
                    "message": "startDate ve endDate zorunlu.",
                    "objects": None,
                })

            if endDate <= startDate:
                return Response({
                    "result": False,
                    "message": "endDate > startDate olmalı.",
                    "objects": None,
                })

            qs = (
                Reading.objects
                .filter(
                    sensor__parameter__station__sais_cabinets__device_id=station_id,
                    time_iso__range=[startDate, endDate],
                )
                .select_related("sensor", "sensor__parameter", "status")
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
                aggfunc="mean",
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
        return Reading.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None,
                })

            last_record = (
                Reading.objects
                .filter(sensor__parameter__station__sais_cabinets__device_id=station_id)
                .order_by("-time_iso")
                .first()
            )
            if not last_record:
                return Response({"result": True, "message": None, "objects": []})

            last_time = last_record.time_iso

            qs = (
                Reading.objects
                .filter(
                    sensor__parameter__station__sais_cabinets__device_id=station_id,
                    time_iso=last_time,
                )
                .select_related("sensor", "sensor__parameter", "status")
            )

            if not qs.exists():
                return Response({"result": True, "message": None, "objects": []})

            ser = ReadsDataSerializer(qs, many=True)
            df = pd.DataFrame(ser.data)

            if df.empty:
                return Response({"result": True, "message": None, "objects": []})

            df_pivot = df.pivot_table(
                index="ReadTime",
                columns="ParameterName",
                values="Value",
                aggfunc="mean",
            )

            for pname in df["ParameterName"].unique():
                status_col = (
                    df[df["ParameterName"] == pname]
                    .set_index("ReadTime")["Status"]
                    .rename(f"{pname}_Status")
                )
                df_pivot = df_pivot.join(status_col, how="left")

            df_pivot = df_pivot.reset_index()

            response_data = df_to_json_records(df_pivot)

            return Response({"result": True, "message": None, "objects": response_data})

        except Exception as e:
            return Response({"result": False, "message": str(e), "objects": None})


# Son Veri saatini döndüren servis
class GetLastReadTimeView(generics.ListAPIView):

    def get_queryset(self):
        return Reading.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None,
                })

            last_record = (
                Reading.objects
                .filter(sensor__parameter__station__sais_cabinets__device_id=station_id)
                .order_by("-time_iso")
                .first()
            )

            if not last_record:
                return Response({"result": True, "message": None, "objects": None})

            return Response({
                "result": True,
                "message": None,
                "objects": last_record.time_iso.isoformat(),
            })

        except Exception as e:
            return Response({
                "result": False,
                "message": str(e),
                "objects": None,
            })


# Kanal Bilgileri Döndürme Servisi
class GetChannelInfoView(generics.ListAPIView):
    serializer_class = ChannelInfoSerializer

    def get_queryset(self):
        return Sensor.objects.select_related("parameter", "latest")

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None,
                })

            qs = self.get_queryset().filter(
                parameter__station__sais_cabinets__device_id=station_id,
            )

            if not qs.exists():
                return Response({"result": True, "message": None, "objects": []})

            ser = self.get_serializer(qs, many=True)
            return Response({
                "result": True,
                "message": None,
                "objects": ser.data,
            })
        except Exception as e:
            return Response({
                "result": False,
                "message": str(e),
                "objects": None,
            })


# İstasyon Bilgileri Döndürme Servisi
class GetStationInformationView(generics.ListAPIView):
    serializer_class = StationInfoSerializer

    def get_queryset(self):
        return SaisCabinet.objects.select_related("station").all()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            if not station_id:
                return Response({"result": False, "message": "stationId parametresi zorunlu.", "objects": None})

            qs = self.get_queryset().filter(device_id=station_id).first()
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
                    "objects": None,
                })

            device = SaisCabinet.objects.filter(device_id=station_id).select_related("station").first()
            if not device:
                return Response({
                    "result": False,
                    "message": "Geçersiz stationId.",
                    "objects": None,
                })

            start_dt = parse_datetime(startDate) if startDate else None
            end_dt = parse_datetime(endDate) if endDate else None

            if start_dt and end_dt and end_dt <= start_dt:
                return Response({
                    "result": False,
                    "message": "endDate > startDate olmalı.",
                    "objects": None,
                })

            qs = Calibration.objects.filter(sensor__parameter__station_id=device.station_id)

            if start_dt and end_dt:
                qs = qs.filter(time_iso__range=[start_dt, end_dt])

            qs = qs.select_related("sensor", "sensor__parameter").order_by("-time_iso")

            ser = self.get_serializer(qs, many=True)
            return Response({"result": True, "message": None, "objects": ser.data})

        except Exception as e:
            return Response({
                "result": False,
                "message": str(e),
                "objects": None,
            })


# Açılma Kapanma tarihlerini bildiren servis
class GetPoweroffView(generics.ListAPIView):
    serializer_class = PoweroffResultSerializer

    def get_queryset(self):
        return PowerOff.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None,
                })

            device = SaisCabinet.objects.filter(device_id=station_id).select_related("station").first()
            if not device:
                return Response({
                    "result": False,
                    "message": "Geçersiz stationId.",
                    "objects": None,
                })

            startDate = request.GET.get("startDate")
            endDate = request.GET.get("endDate")

            qs = PowerOff.objects.filter(station=device.station)

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
                "objects": None,
            })


# Log kayıtları görünütüleme servisi
class GetLogView(generics.ListAPIView):
    serializer_class = LogResultSerializer

    def get_queryset(self):
        return SystemLog.objects.none()

    def list(self, request, *args, **kwargs):
        try:
            station_id = request.GET.get("stationId")
            startDate = request.GET.get("startDate")
            endDate = request.GET.get("endDate")

            if not station_id:
                return Response({
                    "result": False,
                    "message": "stationId parametresi zorunlu.",
                    "objects": None,
                })

            device = SaisCabinet.objects.filter(device_id=station_id).select_related("station").first()
            if not device:
                return Response({
                    "result": False,
                    "message": "Geçersiz stationId.",
                    "objects": None,
                })

            qs = SystemLog.objects.filter(station=device.station)

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
                "objects": None,
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
                    "objects": None,
                })

            device = SaisCabinet.objects.filter(device_id=station_id).select_related("station").first()
            if not device:
                return Response({
                    "result": False,
                    "message": "Geçersiz StationId.",
                    "objects": None,
                })

            sensor = device.station.sample_request_sensor
            if not sensor:
                return Response({
                    "result": False,
                    "message": "İstasyonun numune alma sensörü tanımlı değil (Station.sample_request_sensor).",
                    "objects": None,
                })

            # Bakanlık Numune Talebi tipi SAIS seed'iyle birlikte yaratılır.
            request_type = RequestType.objects.filter(code="ministry_sample").first()

            OutputRequest.objects.create(
                sensor=sensor,
                value=1,
                request_type=request_type,
                request_code=code,
                is_completed=False,
            )

            # TODO: Cihazın gerçekten tetiklenmesi için PLC/RTU'ya komut gönderimi eklenecek.

            return Response({
                "result": True,
                "message": None,
                "objects": True,
            })

        except Exception as e:
            return Response({
                "result": False,
                "message": str(e),
                "objects": None,
            })
