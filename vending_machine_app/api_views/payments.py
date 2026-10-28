from .common_imports import *



def allowed_business(request, business_id, manage=False):
    if not business_id:
        return None

    membership = BusinessMembership.objects.filter(
        business_id=business_id,
        user=request.user,
        is_active=True,
    )

    if manage:
        membership = membership.filter(
            role__in=["OWNER", "MANAGER"]
        )

    return membership.first()


def payment_business_queryset(business_id):
    # Assumes CoffeePayment.order.machine.business FK chain.
    # Adapt if your CoffeeOrder has a direct business FK.
    return CoffeePayment.objects.filter(
        order__machine__business_id=business_id
    ).select_related("order", "order__machine")



def payment_payload(payment):
    order = payment.order

    return {
        "id": payment.id,
        "reference": getattr(order, "reference", ""),
        "order_reference": getattr(order, "reference", ""),

        "machine_name": getattr(
            order.machine, "name", ""
        ),

        "customer_phone": getattr(
            order, "customer_phone", ""
        ),

        "amount": str(
            getattr(order, "total_amount", 0)
        ),

        "status": payment.status,

        "mpesa_receipt_number": payment.receipt or "",

        "checkout_request_id": (
            payment.checkout_request_id or ""
        ),

        "merchant_request_id": (
            payment.merchant_request_id or ""
        ),

        "result_code": payment.result_code,

        "result_description": (
            payment.result_description or ""
        ),

        "created_at": getattr(
            order, "created_at", None
        ),

        "paid_at": payment.paid_at,

        "payment_method": "MPESA",
    }



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def business_payments(request):
    business_id = request.query_params.get("business")

    if not allowed_business(request, business_id):
        return Response(
            {"detail": "Business access denied."},
            status=status.HTTP_403_FORBIDDEN,
        )

    queryset = payment_business_queryset(business_id).order_by(
        "-paid_at"
    )

    data = [payment_payload(p) for p in queryset]

    return Response({
        "count": len(data),
        "payments": data,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def business_payment_stats(request):
    business_id = request.query_params.get("business")

    if not allowed_business(request, business_id):
        return Response(
            {"detail": "Business access denied."},
            status=status.HTTP_403_FORBIDDEN,
        )

    queryset = payment_business_queryset(business_id)

    successful = {"SUCCESS", "SUCCESSFUL", "PAID"}
    failed = {"FAILED", "CANCELLED"}

    payments = list(queryset)

    total_collected = Decimal("0")
    successful_count = 0
    failed_count = 0
    pending_count = 0

    for payment in payments:
        state = str(payment.status).upper()

        if state in successful:
            successful_count += 1
            total_collected += Decimal(
                str(
                    getattr(
                        payment,
                        "amount",
                        getattr(
                            payment.order,
                            "total_amount",
                            0
                        )
                    )
                )
            )
        elif state in failed:
            failed_count += 1
        else:
            pending_count += 1

    return Response({
        "total_payments": len(payments),
        "successful_payments": successful_count,
        "pending_payments": pending_count,
        "failed_payments": failed_count,
        "total_collected": str(total_collected),
        "currency": "KES",
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def business_payment_detail(request, payment_id):
    business_id = request.query_params.get("business")

    if not allowed_business(request, business_id):
        return Response(
            {"detail": "Business access denied."},
            status=status.HTTP_403_FORBIDDEN,
        )

    payment = get_object_or_404(
        payment_business_queryset(business_id),
        pk=payment_id,
    )

    return Response(payment_payload(payment))


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def daraja_configuration(request):
    business_id = request.query_params.get("business")

    if not allowed_business(
        request, business_id, manage=True
    ):
        return Response(
            {"detail": "Owner or manager access required."},
            status=status.HTTP_403_FORBIDDEN,
        )

    config = DarajaConfiguration.objects.filter(
        business_id=business_id
    ).first()

    if request.method == "GET":
        if not config:
            return Response({
                "configured": False,
                "environment": "sandbox",
                "shortcode": "",
                "callback_url": "",
                "has_consumer_key": False,
                "has_consumer_secret": False,
                "has_passkey": False,
            })

        return Response({
            "configured": True,
            "environment": config.environment,
            "shortcode": config.shortcode,
            "callback_url": config.callback_url,
            "has_consumer_key": bool(config.consumer_key),
            "has_consumer_secret": bool(config.consumer_secret),
            "has_passkey": bool(config.passkey),
        })

    from .payment_serializers import DarajaConfigSerializer

    serializer = DarajaConfigSerializer(
        config,
        data=request.data,
        partial=True,
    )
    serializer.is_valid(raise_exception=True)

    values = serializer.validated_data

    if not config:
        config = DarajaConfiguration(business_id=business_id)

    for field in (
        "environment",
        "shortcode",
        "callback_url",
    ):
        if field in values:
            setattr(config, field, values[field])

    # These assignments assume the model encrypts values
    # through encrypted model fields or property setters.
    for field in (
        "consumer_key",
        "consumer_secret",
        "passkey",
    ):
        if values.get(field):
            setattr(config, field, values[field])

    config.full_clean()
    config.save()

    return Response({
        "message": "M-Pesa configuration saved successfully.",
        "configured": True,
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def test_daraja_configuration(request):
    business_id = request.data.get("business")

    if not allowed_business(
        request, business_id, manage=True
    ):
        return Response(
            {"detail": "Owner or manager access required."},
            status=status.HTTP_403_FORBIDDEN,
        )

    config = DarajaConfiguration.objects.filter(
        business_id=business_id
    ).first()

    if not config:
        return Response(
            {"detail": "Configure Daraja first."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # A configuration-presence check, not an API connection test.
    # Implement a server-side Daraja OAuth request for a real test.
    missing = [
        field
        for field in (
            "consumer_key",
            "consumer_secret",
            "shortcode",
            "passkey",
            "callback_url",
        )
        if not getattr(config, field, None)
    ]

    if missing:
        return Response({
            "success": False,
            "message": "Missing configuration fields.",
            "missing_fields": missing,
        }, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        "success": True,
        "message": (
            "All required configuration fields are present. "
            "Daraja connectivity has not been verified."
        ),
    })
