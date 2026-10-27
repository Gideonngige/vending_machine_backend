
from django.conf import settings
from django.db import models
from django.utils.text import slugify
import uuid
from decimal import Decimal
from django.core.validators import MinValueValidator


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

