from vending_machine_app.models import (
    Business, 
    BusinessMembership, 
    Machine, 
    CoffeeProduct, 
    MachineProduct,
    CoffeeOrder,
    CoffeePayment,
    DispenseJob,
    DarajaConfiguration,
    CoffeePricing,
    
)


from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from vending_machine_app.serializers import RegisterSerializer, UserSerializer


from django.db import transaction
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from vending_machine_app.serializers import (
    BusinessSerializer, 
    MachineSerializer,
    CoffeeProductSerializer,
    MachineProductSerializer,
)
from vending_machine_app.permissions import (
    accessible_businesses,
    require_business_role,
    accessible_businesses,
    require_business_role,
    
)


from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.shortcuts import get_object_or_404
from django.db.models import Q


from decimal import Decimal
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404
from django.utils import timezone


import json
import logging
from django.utils import timezone
from django.db import transaction

logger = logging.getLogger(__name__)
