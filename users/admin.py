from django.contrib import admin
from pip._vendor.rich.status import Status

from api.models import *
from .models import *
# Register your models here.

admin.site.register(CustomUser)
admin.site.register(StationInfo)
admin.site.register(SimInformation)
admin.site.register(Connections)
admin.site.register(Sensors)
admin.site.register(Parameters)
admin.site.register(SensorInstants)
admin.site.register(Status_Codes)
admin.site.register(Reads)
admin.site.register(Calibration)
admin.site.register(Poweroff)
admin.site.register(Sys_Log)
admin.site.register(Log_Types)


































