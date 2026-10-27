
from rest_framework.exceptions import PermissionDenied
from .models import Business, BusinessMembership


def accessible_businesses(user):
    if not user.is_authenticated:
        return Business.objects.none()

    return Business.objects.filter(
        memberships__user=user,
        memberships__is_active=True,
    ).distinct()


def require_business_role(
    user,
    business,
    allowed_roles=("OWNER", "MANAGER"),
):
    if business.status != "ACTIVE":
        raise PermissionDenied("This business is not active.")

    authorized = BusinessMembership.objects.filter(
        business=business,
        user=user,
        is_active=True,
        role__in=allowed_roles,
    ).exists()

    if not authorized:
        raise PermissionDenied(
            "You are not authorized to manage this business."
        )
