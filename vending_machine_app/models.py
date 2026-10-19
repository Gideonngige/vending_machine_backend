
import uuid

from django.db import models
from django.utils import timezone


def generate_order_reference():
    return f"SBP-{uuid.uuid4().hex[:10].upper()}"


class Machine(models.Model):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    location = models.CharField(max_length=200, blank=True)
    is_enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.code} — {self.name}"


class Product(models.Model):
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="products")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    image_url = models.URLField(blank=True)
    volume_ml = models.PositiveIntegerField(default=200)
    is_available = models.BooleanField(default=True)
    stock_quantity = models.PositiveIntegerField(default=100)
    machine_slot = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["machine_slot"]
        constraints = [models.UniqueConstraint(fields=["machine", "machine_slot"], name="unique_machine_slot")]

    def __str__(self):
        return f"{self.name} - KSh {self.price}"


class Order(models.Model):
    machine = models.ForeignKey(Machine, on_delete=models.PROTECT, related_name="orders")
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending payment"
        PAID = "PAID", "Paid"
        DISPENSING = "DISPENSING", "Dispensing"
        READY = "READY", "Ready for collection"
        COLLECTED = "COLLECTED", "Collected"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    reference = models.CharField(
        max_length=20,
        unique=True,
        default=generate_order_reference,
        editable=False,
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name="orders",
    )
    product_name = models.CharField(max_length=100)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    customer_phone = models.CharField(max_length=15)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference} - {self.product_name}"

    def save(self, *args, **kwargs):
        if self._state.adding and self.product_id and not self.machine_id:
            self.machine_id = self.product.machine_id
        # Snapshot product details so future price changes
        # don't change the amount on an existing order.
        if self.product_id and not self.product_name:
            self.product_name = self.product.name
            self.unit_price = self.product.price
            self.total_amount = self.unit_price * self.quantity

        super().save(*args, **kwargs)


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSING = "PROCESSING", "Processing"
        SUCCESS = "SUCCESS", "Successful"
        FAILED = "FAILED", "Failed"

    order = models.OneToOneField(
        Order,
        on_delete=models.PROTECT,
        related_name="payment",
    )
    phone_number = models.CharField(max_length=15)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, default="MPESA")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    merchant_request_id = models.CharField(
        max_length=100, blank=True
    )
    checkout_request_id = models.CharField(
        max_length=100, blank=True, unique=True, null=True
    )
    mpesa_receipt_number = models.CharField(
        max_length=50, blank=True
    )
    result_code = models.CharField(max_length=20, blank=True)
    result_description = models.TextField(blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.order.reference} - {self.status}"


class PaymentAttempt(models.Model):
    """One STK request; keep its identifiers even after another attempt starts."""

    class Status(models.TextChoices):
        PROCESSING = "PROCESSING", "Processing"
        UNKNOWN = "UNKNOWN", "Awaiting reconciliation"
        SUCCESS = "SUCCESS", "Successful"
        FAILED = "FAILED", "Failed"

    id = models.BigAutoField(primary_key=True)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="attempts")
    phone_number = models.CharField(max_length=15)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    checkout_request_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    merchant_request_id = models.CharField(max_length=100, blank=True)
    mpesa_receipt_number = models.CharField(max_length=50, blank=True)
    result_code = models.CharField(max_length=20, blank=True)
    result_description = models.TextField(blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-id"]


class DispenseRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        DISPENSING = "DISPENSING", "Dispensing"
        READY = "READY", "Ready for collection"
        COLLECTED = "COLLECTED", "Collected"
        FAILED = "FAILED", "Failed"

    order = models.OneToOneField(
        Order,
        on_delete=models.PROTECT,
        related_name="dispense_request",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    machine_slot = models.PositiveIntegerField()
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.order.reference} - {self.status}"
