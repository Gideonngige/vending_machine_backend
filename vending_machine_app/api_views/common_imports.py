
import json
import re

from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from vending_machine_app.models import Product, Order, Payment, DispenseRequest


from rest_framework.decorators import api_view, parser_classes, permission_classes

import base64
import logging
from datetime import datetime
from decimal import Decimal

import requests
from django.conf import settings



from rest_framework.decorators import api_view
from rest_framework.permissions import AllowAny
from rest_framework.decorators import permission_classes
from rest_framework.response import Response
from django.db import transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt


from dotenv import load_dotenv
load_dotenv()
import os