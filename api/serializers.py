from rest_framework import serializers

from sais_domain.models import SaisCabinet

from .models import (
    Calibration,
    PowerOff,
    Reading,
    Sensor,
    SystemLog,
)


class ReadsDataSerializer(serializers.ModelSerializer):
    # Reading üzerinden parameter_name alıyoruz.
    ParameterName = serializers.CharField(source="sensor.parameter.parameter_name", read_only=True)
    ReadTime = serializers.DateTimeField(source="time_iso", read_only=True)
    Value = serializers.FloatField(source="value", read_only=True)
    Status = serializers.IntegerField(source="status.id", read_only=True)

    class Meta:
        model = Reading
        fields = ["ParameterName", "ReadTime", "Value", "Status"]


class ChannelInfoSerializer(serializers.ModelSerializer):
    Brand = serializers.CharField(source="brand", allow_null=True)
    BrandModel = serializers.CharField(source="model", allow_null=True)
    FullName = serializers.SerializerMethodField()
    Parameter = serializers.CharField(source="parameter.parameter_name", allow_null=True)
    ParameterText = serializers.CharField(source="parameter.parameter_txt", allow_null=True)
    Unit = serializers.CharField(source="parameter.unit", allow_null=True)
    UnitText = serializers.CharField(source="parameter.unit_txt", allow_null=True)
    IsActive = serializers.BooleanField(source="is_active")
    ChannelMinValue = serializers.FloatField(source="parameter.olcum_min", allow_null=True)
    ChannelMaxValue = serializers.FloatField(source="parameter.olcum_max", allow_null=True)
    ChannelNumber = serializers.IntegerField(source="parameter.channel_number", allow_null=True)
    CalibrationFormulaA = serializers.FloatField(source="latest.factorA", allow_null=True)
    CalibrationFormulaB = serializers.FloatField(source="latest.factorB", allow_null=True)
    SerialNumber = serializers.CharField(source="serial_number", allow_null=True)

    class Meta:
        model = Sensor
        fields = [
            "id", "Brand", "BrandModel", "FullName", "Parameter", "ParameterText",
            "Unit", "UnitText", "IsActive",
            "ChannelMinValue", "ChannelMaxValue", "ChannelNumber",
            "CalibrationFormulaA", "CalibrationFormulaB",
            "SerialNumber",
        ]

    def get_FullName(self, obj):
        if not obj.parameter:
            return str(obj.id)
        param = obj.parameter.parameter_name or ""
        return f"{obj.parameter.device_channel_id} | {param}"


class StationInfoSerializer(serializers.ModelSerializer):
    StationId = serializers.CharField(source="device_id")
    Code = serializers.CharField(source="code")
    Name = serializers.CharField(source="name")
    DataPeriodMinute = serializers.IntegerField(source="data_period")
    LastDataDate = serializers.SerializerMethodField()
    ConnectionDomainAddress = serializers.CharField(source="station.domain")
    ConnectionPort = serializers.IntegerField(source="station.port")
    ConnectionUser = serializers.CharField(source="auth_username")
    ConnectionPassword = serializers.CharField(source="auth_secret")
    Company = serializers.CharField(source="station.company")
    BirtDate = serializers.SerializerMethodField()
    SetupDate = serializers.SerializerMethodField()
    Adress = serializers.CharField(source="station.address", allow_null=True)
    Software = serializers.SerializerMethodField()

    class Meta:
        model = SaisCabinet
        fields = [
            "StationId", "Code", "Name", "DataPeriodMinute",
            "LastDataDate", "ConnectionDomainAddress", "ConnectionPort",
            "ConnectionUser", "ConnectionPassword", "Company",
            "BirtDate", "SetupDate", "Adress", "Software",
        ]

    def get_LastDataDate(self, obj):
        return None

    def get_BirtDate(self, obj):
        return obj.created_at.isoformat() if obj.created_at else None

    def get_SetupDate(self, obj):
        return obj.station.created_at.isoformat() if obj.station and obj.station.created_at else None

    def get_Software(self, obj):
        return None


class CalibrationResultSerializer(serializers.ModelSerializer):
    StationId = serializers.SerializerMethodField()
    DBColumnName = serializers.CharField(source="sensor.parameter.parameter_name")
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
            "ResultFactor", "ResultZero", "ResultSpan", "Result",
        ]

    def get_StationId(self, obj):
        if not obj.sensor or not obj.sensor.parameter:
            return None
        station_id = obj.sensor.parameter.station_id
        if not station_id:
            return None
        device = SaisCabinet.objects.filter(station_id=station_id).first()
        return device.device_id if device else None

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
        model = PowerOff
        fields = ["StationId", "StartDate", "EndDate"]

    def get_StationId(self, obj):
        device = obj.station.sais_cabinets.first() if obj.station_id else None
        return device.device_id if device else None


class LogResultSerializer(serializers.ModelSerializer):
    logTitle = serializers.CharField(source="type.name", read_only=True)
    LogDescription = serializers.CharField(source="description", read_only=True)
    LogCreatedDate = serializers.DateTimeField(source="time_iso", read_only=True)

    class Meta:
        model = SystemLog
        fields = ["logTitle", "LogDescription", "LogCreatedDate"]
