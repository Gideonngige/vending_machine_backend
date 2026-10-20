"""Inventory operations run inside a transaction with the product locked first."""
from datetime import timedelta

from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from .models import StockReservation


def held_quantity(product, *, exclude_order=None, now=None):
    holds = StockReservation.objects.filter(
        order__product=product, status=StockReservation.Status.ACTIVE,
        expires_at__gt=now or timezone.now(),
    )
    if exclude_order:
        holds = holds.exclude(order=exclude_order)
    return holds.aggregate(total=Sum("quantity"))["total"] or 0


def available_quantity(product, **kwargs):
    return max(0, product.stock_quantity - held_quantity(product, **kwargs))


def reserve(order, product):
    """Create/reacquire a hold, including for legacy orders and failed retries."""
    now = timezone.now()
    hold = StockReservation.objects.filter(order=order).first()
    if hold and hold.status == StockReservation.Status.CONSUMED:
        return False
    if available_quantity(product, exclude_order=order, now=now) < order.quantity:
        return False
    if hold and hold.status == StockReservation.Status.ACTIVE and hold.expires_at > now:
        return True
    StockReservation.objects.update_or_create(order=order, defaults={
        "quantity": order.quantity, "status": StockReservation.Status.ACTIVE,
        "expires_at": now + timedelta(minutes=settings.STOCK_RESERVATION_MINUTES),
    })
    product.save(update_fields=["updated_at"])
    return True


def release(order, product):
    changed = StockReservation.objects.filter(order=order, status="ACTIVE").update(
        status=StockReservation.Status.RELEASED, updated_at=timezone.now(),
    )
    if changed:
        product.save(update_fields=["updated_at"])


def consume(order, product):
    """Settle once; late payments can only use stock unreserved by other orders."""
    now = timezone.now()
    hold = StockReservation.objects.filter(order=order).first()
    if order.status == "CANCELLED" or available_quantity(product, exclude_order=order, now=now) < order.quantity:
        release(order, product)
        return False
    product.stock_quantity -= order.quantity
    product.save(update_fields=["stock_quantity", "updated_at"])
    if hold:
        hold.status = StockReservation.Status.CONSUMED
        hold.save(update_fields=["status", "updated_at"])
    return True
