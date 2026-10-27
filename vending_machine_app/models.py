
from django.conf import settings
from django.db import models
from django.utils.text import slugify
import uuid
from decimal import Decimal
from django.core.validators import MinValueValidator
from decimal import Decimal

import secrets


class Business(models.Model):
    STATUS_CHOICES = [
        ("ACTIVE", "Active"),
        ("SUSPENDED", "Suspended"),
    ]

    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_coffee_businesses",
    )

    description = models.TextField(blank=True)
    logo_url = models.URLField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    county = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=255, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="ACTIVE",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "coffee-business"
            self.slug = f"{base}-{uuid.uuid4().hex[:6]}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class BusinessMembership(models.Model):
    ROLE_CHOICES = [
        ("OWNER", "Owner"),
        ("MANAGER", "Manager"),
        ("STAFF", "Staff"),
    ]

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="memberships",
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="coffee_business_memberships",
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="STAFF",
    )

    is_active = models.BooleanField(default=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["business", "user"],
                name="unique_coffee_business_member",
            )
        ]

    def __str__(self):
        return f"{self.user} - {self.business.name}"



class Machine(models.Model):
    MACHINE_TYPES = [
        ("VIRTUAL", "Virtual"),
        ("PHYSICAL", "Physical"),
    ]

    STATUS_CHOICES = [
        ("ONLINE", "Online"),
        ("OFFLINE", "Offline"),
        ("MAINTENANCE", "Maintenance"),
    ]

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    business = models.ForeignKey(
        Business,
        on_delete=models.PROTECT,
        related_name="machines",
    )

    name = models.CharField(max_length=150)

    machine_code = models.CharField(
        max_length=40,
        unique=True,
        editable=False,
    )

    machine_type = models.CharField(
        max_length=20,
        choices=MACHINE_TYPES,
        default="VIRTUAL",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="OFFLINE",
    )

    location_name = models.CharField(max_length=200)
    county = models.CharField(max_length=100, blank=True)
    address = models.CharField(max_length=255, blank=True)

    total_slots = models.PositiveIntegerField(default=4)

    is_active = models.BooleanField(default=True)
    accepts_online_orders = models.BooleanField(default=True)

    last_seen = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.machine_code:
            self.machine_code = (
                f"COF-{uuid.uuid4().hex[:12].upper()}"
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} - {self.machine_code}"





class CoffeeProduct(models.Model):
    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="products",
    )

    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    image_url = models.URLField(blank=True)

    base_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    volume_ml = models.PositiveIntegerField(default=200)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class MachineProduct(models.Model):
    machine = models.ForeignKey(
        Machine,
        on_delete=models.CASCADE,
        related_name="machine_products",
    )

    product = models.ForeignKey(
        CoffeeProduct,
        on_delete=models.PROTECT,
        related_name="machine_assignments",
    )

    slot_number = models.PositiveIntegerField()

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    stock_quantity = models.PositiveIntegerField(default=0)
    is_available = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["machine", "slot_number"],
                name="unique_machine_slot",
            ),
            models.UniqueConstraint(
                fields=["machine", "product"],
                name="unique_machine_product",
            ),
        ]

    def __str__(self):
        return f"{self.product.name} - {self.machine.name}"




def new_reference():
    return "COF-" + uuid.uuid4().hex[:12].upper()


def new_order_token():
    return secrets.token_urlsafe(32)


class CoffeeOrder(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("PAYMENT_REQUESTED", "Payment requested"),
        ("PAID", "Paid"),
        ("DISPENSING", "Dispensing"),
        ("READY", "Ready"),
        ("COLLECTED", "Collected"),
        ("FAILED", "Failed"),
        ("CANCELLED", "Cancelled"),
    ]

    reference = models.CharField(
        max_length=30,
        unique=True,
        default=new_reference,
        editable=False,
    )
    order_token = models.CharField(
        max_length=100,
        default=new_order_token,
        editable=False,
    )
    machine = models.ForeignKey(
        Machine,
        on_delete=models.PROTECT,
        related_name="coffee_orders",
    )
    machine_product = models.ForeignKey(
        MachineProduct,
        on_delete=models.PROTECT,
    )
    product_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=15)
    quantity = models.PositiveIntegerField(default=1)
    amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )
    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default="PENDING",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.reference


class CoffeePayment(models.Model):
    STATUS_CHOICES = [
        ("PENDING", "Pending"),
        ("PROCESSING", "Processing"),
        ("SUCCESS", "Success"),
        ("FAILED", "Failed"),
    ]

    order = models.OneToOneField(
        CoffeeOrder,
        on_delete=models.PROTECT,
        related_name="payment",
    )
    checkout_request_id = models.CharField(
        max_length=150,
        unique=True,
        null=True,
        blank=True,
    )
    merchant_request_id = models.CharField(
        max_length=150,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="PENDING",
    )
    receipt = models.CharField(max_length=100, blank=True)
    result_code = models.CharField(max_length=20, blank=True)
    result_description = models.TextField(blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)


class DispenseJob(models.Model):
    order = models.OneToOneField(
        CoffeeOrder,
        on_delete=models.PROTECT,
        related_name="dispense_job",
    )
    status = models.CharField(
        max_length=20,
        default="PENDING",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)




# ============================================================
# 1. DARAJA / M-PESA CONFIGURATION
# ============================================================

class DarajaConfiguration(models.Model):

    ENVIRONMENT_CHOICES = [
        ("sandbox", "Sandbox"),
        ("production", "Production"),
    ]

    TRANSACTION_TYPE_CHOICES = [
        ("CustomerPayBillOnline", "Paybill"),
        ("CustomerBuyGoodsOnline", "Buy Goods / Till"),
    ]

    # --------------------------------------------------------
    # Business / Tenant
    # --------------------------------------------------------

    business = models.OneToOneField(
        Business,
        on_delete=models.CASCADE,
        related_name="daraja_configuration"
    )

    # --------------------------------------------------------
    # Daraja Environment
    # --------------------------------------------------------

    environment = models.CharField(
        max_length=20,
        choices=ENVIRONMENT_CHOICES,
        default="sandbox"
    )

    # --------------------------------------------------------
    # Safaricom Daraja Credentials
    # --------------------------------------------------------

    consumer_key = models.CharField(
        max_length=255
    )

    consumer_secret = models.CharField(
        max_length=255
    )

    passkey = models.CharField(
        max_length=255
    )

    short_code = models.CharField(
        max_length=50
    )

    # --------------------------------------------------------
    # Transaction Type
    # --------------------------------------------------------

    transaction_type = models.CharField(
        max_length=50,
        choices=TRANSACTION_TYPE_CHOICES,
        default="CustomerPayBillOnline"
    )

    # --------------------------------------------------------
    # Callback Configuration
    # --------------------------------------------------------

    callback_url = models.URLField(
        blank=True,
        null=True
    )

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------

    is_active = models.BooleanField(
        default=False
    )

    is_verified = models.BooleanField(
        default=False
    )

    last_verified_at = models.DateTimeField(
        blank=True,
        null=True
    )

    # --------------------------------------------------------
    # Timestamps
    # --------------------------------------------------------

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    def __str__(self):
        return (
            f"M-Pesa Configuration - "
            f"{self.business.name}"
        )




# ============================================================
# COFFEE PRICING CONFIGURATION
# ============================================================

class CoffeePricing(models.Model):

    # --------------------------------------------------------
    # Cup Sizes
    # --------------------------------------------------------

    CUP_SIZES = [
        ("SMALL", "Small"),
        ("MEDIUM", "Medium"),
        ("LARGE", "Large"),
        ("EXTRA_LARGE", "Extra Large"),
    ]

    # --------------------------------------------------------
    # Milk Options
    # --------------------------------------------------------

    MILK_OPTIONS = [
        ("NONE", "No Milk"),
        ("REGULAR", "Regular Milk"),
        ("OAT", "Oat Milk"),
        ("ALMOND", "Almond Milk"),
    ]

    # --------------------------------------------------------
    # Sugar Levels
    # --------------------------------------------------------

    SUGAR_LEVELS = [
        ("NONE", "No Sugar"),
        ("LOW", "Low Sugar"),
        ("NORMAL", "Normal Sugar"),
        ("EXTRA", "Extra Sugar"),
    ]

    # --------------------------------------------------------
    # Business / Tenant
    # --------------------------------------------------------

    business = models.ForeignKey(
        Business,
        on_delete=models.CASCADE,
        related_name="coffee_pricing"
    )

    # --------------------------------------------------------
    # Coffee Product
    # --------------------------------------------------------

    product = models.ForeignKey(
        CoffeeProduct,
        on_delete=models.CASCADE,
        related_name="pricing_options"
    )

    # --------------------------------------------------------
    # Cup Size
    # --------------------------------------------------------

    cup_size = models.CharField(
        max_length=20,
        choices=CUP_SIZES,
        default="MEDIUM"
    )

    # --------------------------------------------------------
    # Milk Option
    # --------------------------------------------------------

    milk_option = models.CharField(
        max_length=20,
        choices=MILK_OPTIONS,
        default="REGULAR"
    )

    # --------------------------------------------------------
    # Sugar Level
    # --------------------------------------------------------

    sugar_level = models.CharField(
        max_length=20,
        choices=SUGAR_LEVELS,
        default="NORMAL"
    )

    # --------------------------------------------------------
    # Coffee Price
    # --------------------------------------------------------

    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[
            MinValueValidator(Decimal("0.01"))
        ]
    )

    # --------------------------------------------------------
    # Optional Minimum Charge
    # --------------------------------------------------------

    minimum_charge = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[
            MinValueValidator(Decimal("0.00"))
        ]
    )

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------

    is_active = models.BooleanField(
        default=True
    )

    # --------------------------------------------------------
    # Timestamps
    # --------------------------------------------------------

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    # --------------------------------------------------------
    # Constraints
    # --------------------------------------------------------

    class Meta:

        ordering = [
            "product__name",
            "cup_size",
            "milk_option",
            "sugar_level",
        ]

        constraints = [
            models.UniqueConstraint(
                fields=[
                    "business",
                    "product",
                    "cup_size",
                    "milk_option",
                    "sugar_level",
                ],
                name="unique_business_coffee_pricing"
            )
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        super().clean()

        if (
            self.business_id
            and self.product_id
            and self.product.business_id != self.business_id
        ):
            raise ValidationError({
                "product": (
                    "Coffee product must belong "
                    "to the selected business."
                )
            })

    def __str__(self):
        return (
            f"{self.business.name} - "
            f"{self.product.name} - "
            f"{self.cup_size} - "
            f"{self.milk_option} - "
            f"{self.sugar_level} - "
            f"KES {self.price}"
        )






