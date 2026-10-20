from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view

from vending_machine_app.models import Machine, Product


@api_view(["GET"])
def product_list(request):
    machine = get_object_or_404(Machine, code=request.query_params.get("machine", "MACHINE-001"), is_enabled=True)
    products = Product.objects.filter(is_available=True, machine=machine)

    data = [
        {
            "id": product.id,
            "name": product.name,
            "description": product.description,
            "price": str(product.price),
            "image_url": product.image_url,
            "volume_ml": product.volume_ml,
            "machine_slot": product.machine_slot,
            "machine_code": machine.code,
            "available": product.stock_quantity > 0,
        }
        for product in products
    ]

    return JsonResponse({"success": True, "products": data})