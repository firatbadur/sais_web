from rest_framework import serializers
from users.models import CustomUser
from datetime import datetime
from datetime import date
from django.utils.timesince import timesince
from django.shortcuts import get_object_or_404
import logging
from .models import *


logger = logging.getLogger(__name__)


# class SimDataSerializer(serializers.ModelSerializer):
#
#     # 'ph_s' alanını 'phStatus' olarak yeniden adlandırıyoruz
#     ph = serializers.FloatField(source='ph_v')
#     ph_Status = serializers.IntegerField(source='ph_s')
#     Iletkenlik = serializers.FloatField(source='il_v')
#     Iletkenlik_Status = serializers.IntegerField(source='il_s')
#     CozunmusOksijen = serializers.FloatField(source='coz_v')
#     CozunmusOksijen_Status = serializers.IntegerField(source='coz_s')
#     Debi = serializers.FloatField(source='debi_v')
#     Debi_Status = serializers.IntegerField(source='debi_s')
#     Sicaklik = serializers.FloatField(source='sic_v')
#     Sicaklik_Status = serializers.IntegerField(source='sic_s')
#     AkisHizi = serializers.FloatField(source='akis_v')
#     AkisHizi_Status = serializers.IntegerField(source='akis_s')
#     KOi = serializers.FloatField(source='koi_v')
#     KOi_Status = serializers.IntegerField(source='koi_s')
#     AKM = serializers.FloatField(source='akm_v')
#     AKM_Status = serializers.IntegerField(source='akm_s')
#     ReadTime = serializers.DateTimeField(source='time_iso')
#
#     class Meta:
#         model = Sim_Data
#         fields = ['ph','ph_Status','Iletkenlik','Iletkenlik_Status','CozunmusOksijen','CozunmusOksijen_Status',
#                   'Debi','Debi_Status','Sicaklik','Sicaklik_Status',
#                   'AkisHizi','AkisHizi_Status','KOi','KOi_Status','AKM','AKM_Status','ReadTime']

class ReadsDataSerializer(serializers.ModelSerializer):
    # channel üzerinden parameter_name alıyoruz
    ParameterName = serializers.CharField(source="channel.parameters.parameter_name", read_only=True)
    ReadTime = serializers.DateTimeField(source="time_iso", read_only=True)
    Value = serializers.FloatField(source="value", read_only=True)
    Status = serializers.IntegerField(source="status.id", read_only=True)

    class Meta:
        model = Reads
        fields = ["ParameterName", "ReadTime", "Value", "Status"]

class ChannelInfoSerializer(serializers.ModelSerializer):

    Brand = serializers.CharField(source="brand", allow_null=True)
    BrandModel = serializers.CharField(source="model", allow_null=True)
    FullName = serializers.SerializerMethodField()
    Parameter = serializers.CharField(source="parameters.parameter_name", allow_null=True)
    ParameterText = serializers.CharField(source="parameters.parameter_txt", allow_null=True)
    Unit = serializers.CharField(source="parameters.unit", allow_null=True)
    UnitText = serializers.CharField(source="parameters.unit_txt", allow_null=True)
    IsActive = serializers.BooleanField(source="is_active")
    ChannelMinValue = serializers.IntegerField(source="parameters.olcum_min", allow_null=True)
    ChannelMaxValue = serializers.IntegerField(source="parameters.olcum_max", allow_null=True)
    ChannelNumber = serializers.IntegerField(source="parameters.channel_number", allow_null=True)
    CalibrationFormulaA = serializers.FloatField(source="sensorinstants.factorA", allow_null=True)
    CalibrationFormulaB = serializers.FloatField(source="sensorinstants.factorB", allow_null=True)
    SerialNumber = serializers.CharField(source="serial_number", allow_null=True)

    class Meta:
        model = Sensors
        fields = [
            "id", "Brand", "BrandModel", "FullName", "Parameter", "ParameterText",
            "Unit", "UnitText", "IsActive",
            "ChannelMinValue", "ChannelMaxValue", "ChannelNumber",
            "CalibrationFormulaA", "CalibrationFormulaB",
            "SerialNumber"
        ]

    def get_FullName(self, obj):
        param = obj.parameters.parameter_name if obj.parameters else ""
        return f"{obj.parameters.sim_channel} | {param}" if obj.parameters else str(obj.id)

class StationInfoSerializer(serializers.ModelSerializer):

    StationId = serializers.CharField(source="sim_id")
    Code = serializers.CharField(source="code")
    Name = serializers.CharField(source="name")
    DataPeriodMinute = serializers.IntegerField(source="data_period")
    LastDataDate = serializers.SerializerMethodField()
    ConnectionDomainAddress = serializers.CharField(source="station.domain")
    ConnectionPort = serializers.IntegerField(source="station.port")
    ConnectionUser = serializers.CharField(source="username")
    ConnectionPassword = serializers.CharField(source="password")
    Company = serializers.CharField(source="station.company")
    BirtDate = serializers.SerializerMethodField()
    SetupDate = serializers.SerializerMethodField()
    Adress = serializers.CharField(source="station.address", allow_null=True)
    Software = serializers.SerializerMethodField()

    class Meta:
        model = SimInformation
        fields = [
            "StationId", "Code", "Name", "DataPeriodMinute",
            "LastDataDate", "ConnectionDomainAddress", "ConnectionPort",
            "ConnectionUser", "ConnectionPassword", "Company",
            "BirtDate", "SetupDate", "Adress", "Software"
        ]

    def get_LastDataDate(self, obj):
        # Burada Reads tablosundan son veri tarihi çekilebilir
        # örnek: Reads.objects.filter(channel__sensor__sim_channel=obj.sim_id).order_by("-time_iso").first()
        return None

    def get_BirtDate(self, obj):
        return obj.created_at.isoformat() if obj.created_at else None

    def get_SetupDate(self, obj):
        return obj.station.created_at.isoformat() if obj.station and obj.station.created_at else None

    def get_Software(self, obj):
        return None

class CalibrationResultSerializer(serializers.ModelSerializer):
    StationId = serializers.SerializerMethodField()
    DBColumnName = serializers.CharField(source="channel.sensor.parameter_name")
    CalibrationDate = serializers.DateTimeField(source="time_iso")

    ZeroRef = serializers.SerializerMethodField()
    ZeroMeas = serializers.SerializerMethodField()
    ZeroDiff = serializers.SerializerMethodField()
    ZeroSTD = serializers.SerializerMethodField()

    SpanRef = serializers.SerializerMethodField()
    SpanMeas = serializers.SerializerMethodField()
    SpanDiff = serializers.SerializerMethodField()
    SpanSTD = serializers.SerializerMethodField()

    ResultFactor = serializers.SerializerMethodField()
    ResultZero = serializers.SerializerMethodField()
    ResultSpan = serializers.SerializerMethodField()
    Result = serializers.SerializerMethodField()

    class Meta:
        model = Calibration
        fields = [
            "StationId",
            "DBColumnName",
            "CalibrationDate",
            "ZeroRef", "ZeroMeas", "ZeroDiff", "ZeroSTD",
            "SpanRef", "SpanMeas", "SpanDiff", "SpanSTD",
            "ResultFactor", "ResultZero", "ResultSpan", "Result"
        ]

    def get_StationId(self, obj):
        # Calibration → channel → sensor → station_id üzerinden sim_id bul
        sim_info = SimInformation.objects.filter(station_id=obj.channel.sensor.station_id).first()
        return sim_info.sim_id if sim_info else None

    # ---- ZERO alanları ----
    def get_ZeroRef(self, obj):
        return obj.cal_ref if obj.type == 0 else None

    def get_ZeroMeas(self, obj):
        return obj.cal_average if obj.type == 0 else None

    def get_ZeroDiff(self, obj):
        if obj.type == 0 and obj.cal_ref:
            return ((obj.cal_average - obj.cal_ref) / obj.cal_ref) * 100
        return None

    def get_ZeroSTD(self, obj):
        return obj.cal_std if obj.type == 0 else None

    # ---- SPAN alanları ----
    def get_SpanRef(self, obj):
        return obj.cal_ref if obj.type == 1 else None

    def get_SpanMeas(self, obj):
        return obj.cal_average if obj.type == 1 else None

    def get_SpanDiff(self, obj):
        if obj.type == 1 and obj.cal_ref:
            return ((obj.cal_average - obj.cal_ref) / obj.cal_ref) * 100
        return None

    def get_SpanSTD(self, obj):
        return obj.cal_std if obj.type == 1 else None

    # ---- SONUÇ alanları ----
    def get_ResultFactor(self, obj):
        if obj.type == 1 and obj.cal_ref:
            return obj.cal_average / obj.cal_ref
        return None

    def get_ResultZero(self, obj):
        return obj.is_valid if obj.type == 0 else None

    def get_ResultSpan(self, obj):
        return obj.is_valid if obj.type == 1 else None

    def get_Result(self, obj):
        return obj.is_valid

class PoweroffResultSerializer(serializers.ModelSerializer):
    StationId = serializers.SerializerMethodField()
    StartDate = serializers.DateTimeField(source="start_date")
    EndDate = serializers.DateTimeField(source="end_date")

    class Meta:
        model = Poweroff
        fields = ["StationId", "StartDate", "EndDate"]

    def get_StationId(self, obj):
        # Poweroff → StationInfo → SimInformation.sim_id
        sim_info = obj.station.siminformation_set.first()
        return sim_info.sim_id if sim_info else None

class LogResultSerializer(serializers.ModelSerializer):
    logTitle = serializers.CharField(source="type.name", read_only=True)
    LogDescription = serializers.CharField(source="description", read_only=True)
    LogCreatedDate = serializers.DateTimeField(source="time_iso", read_only=True)

    class Meta:
        model = Sys_Log
        fields = ["logTitle", "LogDescription", "LogCreatedDate"]

