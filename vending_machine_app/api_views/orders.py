
from .common_imports import *


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def business_orders(request):
    business_id = request.query_params.get("business")

    if not business_id:
        return Response(
            {"detail": "Business ID is required."},
            status=status.HTTP_400_BAD_REQUEST
        )

    membership_exists = BusinessMembership.objects.filter(
        business_id=business_id,
        user=request.user,
        is_active=True,
    ).exists()

    if not membership_exists:
        return Response(
            {"detail": "You do not have access to this business."},
            status=status.HTTP_403_FORBIDDEN
        )

    orders = (
        CoffeeOrder.objects
        .filter(machine__business_id=business_id)
        .select_related("machine")
        .order_by("-created_at")
    )

    data = []

    for order in orders:
        data.append({
            "id": order.id,
            "reference": order.reference,
            "business": order.machine.business_id,
            "machine": order.machine_id,
            "machine_name": order.machine.name,
            "customer_phone": getattr(
                order, "customer_phone", ""
            ),
            "total_amount": str(
                getattr(order, "total_amount", 0)
            ),
            "status": order.status,
            "payment_status": getattr(
                order, "payment_status", "PENDING"
            ),
            "created_at": order.created_at,
        })

    return Response(
        {
            "count": len(data),
            "orders": data,
        },
        status=status.HTTP_200_OK
    )
