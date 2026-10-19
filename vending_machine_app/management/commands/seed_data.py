"""Add sample menu products without changing existing machine slots."""

from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from vending_machine_app.models import Product


PRODUCTS = (
    (1, "Espresso", "Bold, rich and intense coffee.", "100.00", 60),
    (2, "Cappuccino", "Espresso with steamed milk and creamy foam.", "150.00", 180),
    (3, "Café Latte", "Smooth espresso with steamed milk.", "180.00", 250),
    (4, "Black Coffee", "Freshly brewed black coffee.", "80.00", 200),
)


class Command(BaseCommand):
    help = (
        "Seed four sample coffee products. Existing machine slots are skipped "
        "so their prices, stock and availability remain unchanged."
    )

    @transaction.atomic
    def handle(self, *args, **options):
        created_count = 0
        for slot, name, description, price, volume in PRODUCTS:
            product, created = Product.objects.get_or_create(
                machine_slot=slot,
                defaults={
                    "name": name,
                    "description": description,
                    "price": Decimal(price),
                    "volume_ml": volume,
                    "stock_quantity": 100,
                    "is_available": True,
                    "image_url": "",
                },
            )
            created_count += int(created)
            action = "Created" if created else "Skipped existing"
            self.stdout.write(f"{action}: slot {slot} — {product.name}")

        self.stdout.write(self.style.SUCCESS(
            f"Seed complete: {created_count} created, "
            f"{len(PRODUCTS) - created_count} existing slots skipped."
        ))
