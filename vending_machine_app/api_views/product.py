from .common_imports import *


@require_GET
def product_list(request):
    products = Product.objects.filter(is_available=True)

    data = [
        {
            "id": product.id,
            "name": product.name,
            "description": product.description,
            "price": str(product.price),
            "image_url": product.image_url,
            "volume_ml": product.volume_ml,
            "machine_slot": product.machine_slot,
            "available": product.stock_quantity > 0,
        }
        for product in products
    ]

    return JsonResponse({"success": True, "products": data})