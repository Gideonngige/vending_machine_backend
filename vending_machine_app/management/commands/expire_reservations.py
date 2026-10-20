from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from vending_machine_app.models import Product, StockReservation


class Command(BaseCommand):
    help = "Mark elapsed stock holds expired. Availability already ignores them automatically."

    def handle(self, *args, **options):
        now = timezone.now()
        product_ids = list(StockReservation.objects.filter(
            status="ACTIVE", expires_at__lte=now,
        ).values_list("order__product_id", flat=True).distinct())
        total = 0
        for pk in sorted(product_ids):
            with transaction.atomic():
                product = Product.objects.select_for_update().get(pk=pk)
                count = StockReservation.objects.filter(
                    order__product=product, status="ACTIVE", expires_at__lte=now,
                ).update(status="EXPIRED", updated_at=now)
                if count:
                    product.save(update_fields=["updated_at"])
                total += count
        self.stdout.write(self.style.SUCCESS(f"Expired {total} stock reservations."))
