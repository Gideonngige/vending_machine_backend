from .common_imports import *



@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def business_list_create(request):
    if request.method == "GET":
        businesses = accessible_businesses(
            request.user
        ).order_by("-created_at")

        serializer = BusinessSerializer(businesses, many=True)
        return Response(serializer.data)

    serializer = BusinessSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    with transaction.atomic():
        business = serializer.save(owner=request.user)

        BusinessMembership.objects.create(
            business=business,
            user=request.user,
            role="OWNER",
            is_active=True,
        )

    return Response(
        {
            "message": "Business registered successfully.",
            "business": BusinessSerializer(business).data,
        },
        status=status.HTTP_201_CREATED,
    )


@api_view(["GET", "PATCH"])
@permission_classes([IsAuthenticated])
def business_detail(request, business_id):
    business = get_object_or_404(
        accessible_businesses(request.user),
        pk=business_id,
    )

    if request.method == "GET":
        return Response(BusinessSerializer(business).data)

    require_business_role(request.user, business)

    serializer = BusinessSerializer(
        business,
        data=request.data,
        partial=True,
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()

    return Response({
        "message": "Business updated successfully.",
        "business": serializer.data,
    })
