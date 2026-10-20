"""Staff reporting: paid revenue and confirmed dispensing are separate metrics."""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from vending_machine_app.models import Machine, Order, Payment


def revenue(payments):
    return str(payments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00"))


def build_sales_summary(machine_code=None):
    today = timezone.localdate()
    zone = timezone.get_current_timezone()

    def midnight(day):
        return timezone.make_aware(datetime.combine(day, time.min), zone)

    payments = Payment.objects.filter(status=Payment.Status.SUCCESS, paid_at__isnull=False)
    orders = Order.objects.all()
    if machine_code:
        machine = get_object_or_404(Machine, code=machine_code)
        payments = payments.filter(order__machine=machine)
        orders = orders.filter(machine=machine)

    def period(start):
        start_at, end_at = midnight(start), midnight(today + timedelta(days=1))
        paid = payments.filter(paid_at__gte=start_at, paid_at__lt=end_at)
        dispensed = orders.filter(
            dispense_request__status__in=["READY", "COLLECTED"],
            dispense_request__completed_at__gte=start_at,
            dispense_request__completed_at__lt=end_at,
        )
        return {
            "revenue": revenue(paid), "paid_orders": paid.count(),
            "coffees_dispensed": dispensed.aggregate(total=Sum("quantity"))["total"] or 0,
        }

    def breakdown(*fields):
        return [
            {**row, "revenue": str(row["revenue"])}
            for row in payments.values(*fields).annotate(
                revenue=Sum("amount"), paid_orders=Count("id"),
            ).order_by(*fields)
        ]

    return {
        "currency": "KES", "timezone": str(zone), "as_of": today.isoformat(),
        "machine": machine_code,
        "unverified_paid_orders": payments.filter(verified_at__isnull=True).count(),
        "today": period(today),
        "week": period(today - timedelta(days=today.weekday())),
        "month": period(today.replace(day=1)),
        "all_time": {"revenue": revenue(payments), "paid_orders": payments.count()},
        "by_product": breakdown("order__product_id", "order__product_name"),
        "by_machine": breakdown("order__machine__code", "order__machine__name"),
    }


@api_view(["GET"])
@permission_classes([IsAdminUser])
def sales_summary(request):
    return Response(build_sales_summary(request.query_params.get("machine")))
