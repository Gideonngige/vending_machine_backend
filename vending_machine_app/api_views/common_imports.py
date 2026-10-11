
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