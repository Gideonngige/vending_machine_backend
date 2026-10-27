
from .common_imports import *




def public_business_data(business):
    return {
        "id": business.id,
        "name": business.name,
        "slug": business.slug,
        "description": business.description,
        "logo_url": business.logo_url,
        "county": business.county,
        "address": business.address,
    }


@api_view(["GET"])
@permission_classes([AllowAny])
def marketplace_businesses(request):
    businesses = Business.objects.filter(
        status="ACTIVE",
        machines__is_active=True,
        machines__accepts_online_orders=True,
    ).distinct()

    search = request.query_params.get("search", "").strip()
    county = request.query_params.get("county", "").strip()

    if search:
        businesses = businesses.filter(
            Q(name__icontains=search)
            | Q(description__icontains=search)
        )

    if county:
        businesses = businesses.filter(county__iexact=county)

    return Response({
        "count": businesses.count(),
        "businesses": [
            public_business_data(b)
            for b in businesses.order_by("name")[:100]
        ],
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def storefront_detail(request, slug):
    business = get_object_or_404(
        Business,
        slug=slug,
        status="ACTIVE",
    )

    machines = Machine.objects.filter(
        business=business,
        is_active=True,
        accepts_online_orders=True,
    ).order_by("name")

    return Response({
        "business": public_business_data(business),
        "machines": [
            {
                "id": str(machine.id),
                "name": machine.name,
                "machine_code": machine.machine_code,
                "location_name": machine.location_name,
                "county": machine.county,
                "status": machine.status,
                "machine_type": machine.machine_type,
            }
            for machine in machines
        ],
    })


@api_view(["GET"])
@permission_classes([AllowAny])
def storefront_machine_products(request, machine_id):
    machine = get_object_or_404(
        Machine,
        pk=machine_id,
        is_active=True,
        accepts_online_orders=True,
        business__status="ACTIVE",
    )

    items = MachineProduct.objects.filter(
        machine=machine,
        is_available=True,
        stock_quantity__gt=0,
        product__is_active=True,
    ).select_related("product").order_by("slot_number")

    return Response({
        "machine": {
            "id": str(machine.id),
            "name": machine.name,
            "location_name": machine.location_name,
            "status": machine.status,
        },
        "products": [
            {
                "id": item.product.id,
                "machine_product_id": item.id,
                "name": item.product.name,
                "description": item.product.description,
                "image_url": item.product.image_url,
                "price": str(item.price),
                "volume_ml": item.product.volume_ml,
                "stock_quantity": item.stock_quantity,
                "slot_number": item.slot_number,
            }
            for item in items
        ],
    })
