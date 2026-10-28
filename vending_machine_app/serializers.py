
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from .models import Business, Machine, CoffeeProduct, MachineProduct, BusinessMembership

from vending_machine_app.permissions import require_business_role, require_business_role

from rest_framework import serializers

from django.db import transaction


User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):

    password = serializers.CharField(
        write_only=True,
        validators=[validate_password],
        style={"input_type": "password"},
    )

    confirm_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    # Business information
    business_name = serializers.CharField(
        max_length=200
    )

    business_phone = serializers.CharField(
        max_length=30
    )

    business_email = serializers.EmailField(
        required=False,
        allow_blank=True
    )

    business_county = serializers.CharField(
        max_length=100
    )

    business_address = serializers.CharField(
        max_length=255
    )

    business_description = serializers.CharField(
        required=False,
        allow_blank=True
    )

    business_logo_url = serializers.URLField(
        required=False,
        allow_blank=True
    )

    class Meta:
        model = User

        fields = [
            "id",
            "username",
            "email",
            "password",
            "confirm_password",
            "business_name",
            "business_phone",
            "business_email",
            "business_county",
            "business_address",
            "business_description",
            "business_logo_url",
        ]

        read_only_fields = ["id"]

    def validate_email(self, value):
        if User.objects.filter(
            email__iexact=value
        ).exists():
            raise serializers.ValidationError(
                "Email is already registered."
            )

        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError({
                "confirm_password": "Passwords do not match."
            })

        return attrs

    @transaction.atomic
    def create(self, validated_data):

        validated_data.pop("confirm_password")

        business_data = {
            "name": validated_data.pop("business_name"),
            "phone": validated_data.pop("business_phone"),
            "email": validated_data.pop("business_email", ""),
            "county": validated_data.pop("business_county"),
            "address": validated_data.pop("business_address"),
            "description": validated_data.pop(
                "business_description", ""
            ),
            "logo_url": validated_data.pop(
                "business_logo_url", ""
            ),
        }

        # 1. Create user
        user = User.objects.create_user(
            **validated_data
        )

        # 2. Create business
        business = Business.objects.create(
            owner=user,
            **business_data
        )

        # 3. Assign owner membership
        BusinessMembership.objects.create(
            business=business,
            user=user,
            role="OWNER",
            is_active=True,
        )

        # Make business available to the view
        self.created_business = business

        return user



class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            "id", "username", "email",
            "first_name", "last_name",
        ]
        read_only_fields = fields


# businesses serializers


class BusinessSerializer(serializers.ModelSerializer):
    class Meta:
        model = Business
        fields = [
            "id", "name", "slug", "description",
            "logo_url", "phone", "email", "county",
            "address", "status", "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id", "slug", "status",
            "created_at", "updated_at",
        ]





class MachineSerializer(serializers.ModelSerializer):
    business = serializers.PrimaryKeyRelatedField(
        queryset=Business.objects.all()
    )

    class Meta:
        model = Machine
        fields = [
            "id", "business", "name", "machine_code",
            "machine_type", "status", "location_name",
            "county", "address", "total_slots",
            "is_active", "accepts_online_orders",
            "last_seen", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "machine_code", "last_seen",
            "created_at", "updated_at",
        ]

    def validate(self, attrs):
        request = self.context["request"]

        business = attrs.get(
            "business",
            self.instance.business if self.instance else None,
        )
        require_business_role(request.user, business)

        if self.instance and business.pk != self.instance.business_id:
            raise serializers.ValidationError({
                "business": "Machine ownership cannot be changed."
            })

        machine_type = attrs.get(
            "machine_type",
            self.instance.machine_type if self.instance else "VIRTUAL",
        )
        if machine_type != "VIRTUAL":
            raise serializers.ValidationError({
                "machine_type": "Physical machines are not enabled yet."
            })

        slots = attrs.get(
            "total_slots",
            self.instance.total_slots if self.instance else 4,
        )
        if not 1 <= slots <= 100:
            raise serializers.ValidationError({
                "total_slots": "Must be between 1 and 100."
            })

        if self.instance:
            highest_slot = (
                self.instance.machine_products.order_by(
                    "-slot_number"
                ).values_list("slot_number", flat=True).first()
                or 0
            )
            if slots < highest_slot:
                raise serializers.ValidationError({
                    "total_slots": "Existing products use higher slots."
                })

        return attrs





class CoffeeProductSerializer(serializers.ModelSerializer):
    business = serializers.PrimaryKeyRelatedField(
        queryset=Business.objects.all()
    )

    class Meta:
        model = CoffeeProduct
        fields = [
            "id", "business", "name", "description",
            "image_url", "base_price", "volume_ml",
            "is_active", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "created_at", "updated_at",
        ]

    def validate(self, attrs):
        business = attrs.get(
            "business",
            self.instance.business if self.instance else None,
        )

        require_business_role(
            self.context["request"].user,
            business,
        )

        if self.instance and business.pk != self.instance.business_id:
            raise serializers.ValidationError({
                "business": "Cannot move a product to another business."
            })

        return attrs


class MachineProductSerializer(serializers.ModelSerializer):
    machine = serializers.PrimaryKeyRelatedField(
        queryset=Machine.objects.all()
    )
    product = serializers.PrimaryKeyRelatedField(
        queryset=CoffeeProduct.objects.all()
    )

    product_name = serializers.CharField(
        source="product.name",
        read_only=True,
    )
    machine_name = serializers.CharField(
        source="machine.name",
        read_only=True,
    )

    class Meta:
        model = MachineProduct
        fields = [
            "id", "machine", "machine_name",
            "product", "product_name",
            "slot_number", "price",
            "stock_quantity", "is_available",
        ]
        read_only_fields = [
            "id", "machine_name", "product_name",
        ]

    def validate(self, attrs):
        machine = attrs.get(
            "machine",
            self.instance.machine if self.instance else None,
        )
        product = attrs.get(
            "product",
            self.instance.product if self.instance else None,
        )
        slot = attrs.get(
            "slot_number",
            self.instance.slot_number if self.instance else None,
        )

        require_business_role(
            self.context["request"].user,
            machine.business,
        )

        if product.business_id != machine.business_id:
            raise serializers.ValidationError({
                "product": "Product belongs to a different business."
            })

        if not machine.is_active:
            raise serializers.ValidationError({
                "machine": "Machine is inactive."
            })

        if self.instance and (
            machine.pk != self.instance.machine_id
            or product.pk != self.instance.product_id
        ):
            raise serializers.ValidationError(
                "Cannot reassign an existing machine product."
            )

        if not 1 <= slot <= machine.total_slots:
            raise serializers.ValidationError({
                "slot_number": "Invalid slot number."
            })

        # Friendly errors before hitting database constraints.
        duplicate_slot = MachineProduct.objects.filter(
            machine=machine,
            slot_number=slot,
        )
        duplicate_product = MachineProduct.objects.filter(
            machine=machine,
            product=product,
        )

        if self.instance:
            duplicate_slot = duplicate_slot.exclude(pk=self.instance.pk)
            duplicate_product = duplicate_product.exclude(
                pk=self.instance.pk
            )

        if duplicate_slot.exists():
            raise serializers.ValidationError({
                "slot_number": "This slot is already occupied."
            })

        if duplicate_product.exists():
            raise serializers.ValidationError({
                "product": "Product is already assigned to this machine."
            })

        return attrs





# coffee catlog
class CoffeeProductSerializer(serializers.ModelSerializer):
    business = serializers.PrimaryKeyRelatedField(
        queryset=Business.objects.all()
    )

    class Meta:
        model = CoffeeProduct
        fields = [
            "id", "business", "name", "description",
            "image_url", "base_price", "volume_ml",
            "is_active", "created_at", "updated_at",
        ]
        read_only_fields = [
            "id", "created_at", "updated_at",
        ]

    def validate(self, attrs):
        business = attrs.get(
            "business",
            self.instance.business if self.instance else None,
        )

        require_business_role(
            self.context["request"].user,
            business,
        )

        if self.instance and business.pk != self.instance.business_id:
            raise serializers.ValidationError({
                "business": "Cannot move a product to another business."
            })

        return attrs


class MachineProductSerializer(serializers.ModelSerializer):
    machine = serializers.PrimaryKeyRelatedField(
        queryset=Machine.objects.all()
    )
    product = serializers.PrimaryKeyRelatedField(
        queryset=CoffeeProduct.objects.all()
    )

    product_name = serializers.CharField(
        source="product.name",
        read_only=True,
    )
    machine_name = serializers.CharField(
        source="machine.name",
        read_only=True,
    )

    class Meta:
        model = MachineProduct
        fields = [
            "id", "machine", "machine_name",
            "product", "product_name",
            "slot_number", "price",
            "stock_quantity", "is_available",
        ]
        read_only_fields = [
            "id", "machine_name", "product_name",
        ]

    def validate(self, attrs):
        machine = attrs.get(
            "machine",
            self.instance.machine if self.instance else None,
        )
        product = attrs.get(
            "product",
            self.instance.product if self.instance else None,
        )
        slot = attrs.get(
            "slot_number",
            self.instance.slot_number if self.instance else None,
        )

        require_business_role(
            self.context["request"].user,
            machine.business,
        )

        if product.business_id != machine.business_id:
            raise serializers.ValidationError({
                "product": "Product belongs to a different business."
            })

        if not machine.is_active:
            raise serializers.ValidationError({
                "machine": "Machine is inactive."
            })

        if self.instance and (
            machine.pk != self.instance.machine_id
            or product.pk != self.instance.product_id
        ):
            raise serializers.ValidationError(
                "Cannot reassign an existing machine product."
            )

        if not 1 <= slot <= machine.total_slots:
            raise serializers.ValidationError({
                "slot_number": "Invalid slot number."
            })

        # Friendly errors before hitting database constraints.
        duplicate_slot = MachineProduct.objects.filter(
            machine=machine,
            slot_number=slot,
        )
        duplicate_product = MachineProduct.objects.filter(
            machine=machine,
            product=product,
        )

        if self.instance:
            duplicate_slot = duplicate_slot.exclude(pk=self.instance.pk)
            duplicate_product = duplicate_product.exclude(
                pk=self.instance.pk
            )

        if duplicate_slot.exists():
            raise serializers.ValidationError({
                "slot_number": "This slot is already occupied."
            })

        if duplicate_product.exists():
            raise serializers.ValidationError({
                "product": "Product is already assigned to this machine."
            })

        return attrs

