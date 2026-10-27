
from .common_imports import *


def permitted_products(user):
    return CoffeeProduct.objects.filter(
        business__in=accessible_businesses(user)
    )


def permitted_machine_products(user):
    return MachineProduct.objects.filter(
        machine__business__in=accessible_businesses(user)
    ).select_related("machine", "product")


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def product_list_create(request):
    if request.method == "GET":
        products = permitted_products(request.user)

        business_id = request.query_params.get("business")
        if business_id:
            products = products.filter(business_id=business_id)

        serializer = CoffeeProductSerializer(
            products.order_by("name"),
            many=True,
        )
        return Response(serializer.data)

    serializer = CoffeeProductSerializer(
        data=request.data,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    product = serializer.save()

    return Response({
        "message": "Coffee product created.",
        "product": CoffeeProductSerializer(product).data,
    }, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def product_detail(request, product_id):
    product = get_object_or_404(
        permitted_products(request.user),
        pk=product_id,
    )

    if request.method == "GET":
        return Response(CoffeeProductSerializer(product).data)

    require_business_role(request.user, product.business)

    if request.method == "DELETE":
        product.is_active = False
        product.save(update_fields=["is_active", "updated_at"])
        return Response({
            "message": "Product deactivated successfully."
        })

    serializer = CoffeeProductSerializer(
        product,
        data=request.data,
        partial=True,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()

    return Response({
        "message": "Product updated successfully.",
        "product": serializer.data,
    })


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def machine_product_list_create(request):
    if request.method == "GET":
        items = permitted_machine_products(request.user)

        machine_id = request.query_params.get("machine")
        if machine_id:
            items = items.filter(machine_id=machine_id)

        serializer = MachineProductSerializer(
            items.order_by("slot_number"),
            many=True,
        )
        return Response(serializer.data)

    serializer = MachineProductSerializer(
        data=request.data,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    item = serializer.save()

    return Response({
        "message": "Product assigned to machine.",
        "machine_product": MachineProductSerializer(item).data,
    }, status=status.HTTP_201_CREATED)


@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def machine_product_detail(request, item_id):
    item = get_object_or_404(
        permitted_machine_products(request.user),
        pk=item_id,
    )

    if request.method == "GET":
        return Response(MachineProductSerializer(item).data)

    require_business_role(request.user, item.machine.business)

    if request.method == "DELETE":
        item.is_available = False
        item.save(update_fields=["is_available"])
        return Response({
            "message": "Machine product deactivated."
        })

    serializer = MachineProductSerializer(
        item,
        data=request.data,
        partial=True,
        context={"request": request},
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()

    return Response({
        "message": "Machine product updated.",
        "machine_product": serializer.data,
    })
