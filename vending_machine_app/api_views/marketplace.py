from .common_imports import *




def available_machine_queryset():
    return (
        Machine.objects
        .filter(
            is_active=True,
            accepts_online_orders=True,
            status="ONLINE",
            business__status="ACTIVE",
        )
        .select_related("business")
        .annotate(
            available_products=Count(
                "machine_products",
                filter=Q(
                    machine_products__is_available=True,
                    machine_products__stock_quantity__gt=0,
                    machine_products__product__is_active=True,
                ),
                distinct=True,
            ),
            starting_price=Min(
                "machine_products__price",
                filter=Q(
                    machine_products__is_available=True,
                    machine_products__stock_quantity__gt=0,
                    machine_products__product__is_active=True,
                ),
            ),
        )
        .filter(available_products__gt=0)
    )


def machine_payload(machine):
    return {
        "id": str(machine.id),
        "name": machine.name,
        "machine_code": machine.machine_code,
        "machine_type": machine.machine_type,
        "status": machine.status,
        "is_active": machine.is_active,
        "accepts_online_orders": machine.accepts_online_orders,
        "location_name": machine.location_name,
        "county": machine.county,
        "address": machine.address,
        "business_id": machine.business_id,
        "business_name": machine.business.name,
        "business_slug": machine.business.slug,
        "available_products": machine.available_products,
        "starting_price": (
            str(machine.starting_price)
            if machine.starting_price is not None
            else None
        ),
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def marketplace_machines(request):
    machines = available_machine_queryset().order_by(
        "business__name",
        "name",
    )

    return Response({
        "count": machines.count(),
        "machines": [
            machine_payload(machine)
            for machine in machines
        ],
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def marketplace_machine_detail(request, machine_id):
    from django.shortcuts import get_object_or_404

    machine = get_object_or_404(
        available_machine_queryset(),
        pk=machine_id,
    )

    products = (
        MachineProduct.objects
        .filter(
            machine=machine,
            is_available=True,
            stock_quantity__gt=0,
            product__is_active=True,
        )
        .select_related("product")
        .order_by("slot_number")
    )

    return Response({
        "machine": machine_payload(machine),
        "products": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "name": item.product.name,
                "description": item.product.description,
                "image_url": item.product.image_url,
                "volume_ml": item.product.volume_ml,
                "price": str(item.price),
                "stock_quantity": item.stock_quantity,
                "slot_number": item.slot_number,
            }
            for item in products
        ],
    })
