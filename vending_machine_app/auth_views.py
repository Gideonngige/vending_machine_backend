"""Cookie-free staff API authentication using access and rotating refresh JWTs."""
from django.contrib.auth import authenticate
from django.db import transaction
from django.views.decorators.cache import never_cache
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.exceptions import APIException
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken


def user_data(user):
    return {"username": user.get_username(), "is_staff": user.is_staff,
            "is_superuser": user.is_superuser}


@never_cache
@api_view(["GET"])
@permission_classes([IsAdminUser])
def current_staff(request):
    return Response({"user": user_data(request.user)})


@never_cache
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def staff_login(request):
    data = request.data
    if not isinstance(data, dict) or not all(
        isinstance(data.get(field), str) and data[field] for field in ("username", "password")
    ):
        return Response({"message": "Username and password are required."}, status=400)
    user = authenticate(request, username=data["username"], password=data["password"])
    if user is None or not user.is_active or not user.is_staff:
        return Response({"message": "Invalid staff credentials."}, status=401)
    refresh = RefreshToken.for_user(user)
    return Response({"user": user_data(user), "access": str(refresh.access_token), "refresh": str(refresh)})


@never_cache
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def staff_refresh(request):
    raw = request.data.get("refresh") if isinstance(request.data, dict) else None
    if not isinstance(raw, str) or not raw:
        return Response({"message": "Refresh token is required."}, status=400)
    try:
        token = RefreshToken(raw)
        # Serialize rotations of the same token on PostgreSQL, then revalidate
        # under the lock so a concurrent replay cannot mint a second successor.
        with transaction.atomic():
            OutstandingToken.objects.select_for_update().get(jti=token["jti"])
            user = JWTAuthentication().get_user(token)
            if not user.is_staff:
                return Response({"message": "Staff access required."}, status=403)
            serializer = TokenRefreshSerializer(data={"refresh": raw})
            serializer.is_valid(raise_exception=True)
            return Response({**serializer.validated_data, "user": user_data(user)})
    except (TokenError, APIException, OutstandingToken.DoesNotExist):
        return Response({"message": "Invalid or expired refresh token. Sign in again."}, status=401)


@never_cache
@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def staff_logout(request):
    raw = request.data.get("refresh") if isinstance(request.data, dict) else None
    if not isinstance(raw, str) or not raw:
        return Response({"message": "Refresh token is required."}, status=400)
    try:
        RefreshToken(raw).blacklist()
    except TokenError:
        # Logout is idempotent for an expired or already revoked refresh token.
        pass
    return Response({"message": "Logged out."})
