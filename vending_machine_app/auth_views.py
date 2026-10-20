import json

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.cache import never_cache


def user_data(user):
    return {"username": user.get_username(), "is_staff": user.is_staff,
            "is_superuser": user.is_superuser}


@require_GET
@ensure_csrf_cookie
@never_cache
def session_status(request):
    user = request.user
    return JsonResponse({"user": user_data(user) if user.is_authenticated and user.is_staff else None,
                         "csrf_token": get_token(request)})


@require_POST
@csrf_protect
@never_cache
def staff_login(request):
    try:
        data = json.loads(request.body)
        if not isinstance(data, dict):
            raise ValueError
        username, password = data.get("username"), data.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"message": "Username and password are required."}, status=400)
    user = authenticate(request, username=username, password=password)
    if user is None or not user.is_active or not user.is_staff:
        return JsonResponse({"message": "Invalid staff credentials."}, status=401)
    login(request, user)
    return JsonResponse({"user": user_data(user), "csrf_token": get_token(request)})


@require_POST
@csrf_protect
@never_cache
def staff_logout(request):
    logout(request)
    return JsonResponse({"message": "Logged out."})


@require_GET
def admin_login(request):
    return redirect(settings.FRONTEND_WEB_URL.rstrip("/") + "/#/owner/login")
