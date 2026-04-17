from django.shortcuts import render,redirect
from django.http import JsonResponse,HttpResponseBadRequest,QueryDict,request
import uuid
from users.models import CustomUser
from django.contrib.auth import authenticate
from django.template.loader import render_to_string
from datetime import *
from django.contrib.auth.hashers import make_password
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.db.models import  F, Value,Func,CharField,Q
# import requests,os
# from requests.auth import HTTPBasicAuth
from django.core.files.storage import FileSystemStorage
import locale
from django.views.decorators.csrf import csrf_exempt
import json
from django.core.serializers.json import DjangoJSONEncoder
from django.core.exceptions import ObjectDoesNotExist
import time
from django.urls import reverse
from pathlib import Path
from django.db.models import Count, Case, When, IntegerField
from django.contrib.auth import login as auth_login
from api.models import *
from django.shortcuts import get_object_or_404
from django.core.files.base import ContentFile
import base64
from django.conf import settings
from django.http import JsonResponse


def home(request, *args, **kwargs):
    return JsonResponse(
        {
            "success": False,
            "error": "Sayfa bulunamadı",
            "code": 404
        },
        status=404  # HTTP status kodu da 404 olsun
    )









