"""Staff catalog configuration. Disable records instead of deleting history."""
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from vending_machine_app.models import Machine, Product


class MachineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Machine
        fields = ("id", "code", "name", "location", "is_enabled")
        read_only_fields = ("id",)

    def validate_code(self, value):
        if self.instance and value != self.instance.code:
            raise serializers.ValidationError("Machine codes cannot be changed; existing kiosks use this code.")
        return value


class ProductSerializer(serializers.ModelSerializer):
    machine_code = serializers.CharField(source="machine.code", read_only=True)
    expected_updated_at = serializers.DateTimeField(write_only=True, required=False)
    price = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("1"))
    stock_quantity = serializers.IntegerField(min_value=0, max_value=2147483647)
    machine_slot = serializers.IntegerField(min_value=1, max_value=2147483647)
    volume_ml = serializers.IntegerField(min_value=1, max_value=2147483647)

    class Meta:
        model = Product
        fields = ("id", "machine", "machine_code", "name", "description", "price",
                  "image_url", "volume_ml", "stock_quantity", "machine_slot", "is_available",
                  "updated_at", "expected_updated_at")
        read_only_fields = ("id", "updated_at")

    def validate_price(self, value):
        if value != value.to_integral_value():
            raise serializers.ValidationError("M-Pesa payments require whole-shilling prices.")
        return value

    def validate(self, attrs):
        expected = attrs.pop("expected_updated_at", None)
        if self.instance:
            if expected != self.instance.updated_at:
                raise serializers.ValidationError({"detail": "This product changed since you loaded it. Reload the catalog before editing again."})
            if attrs.get("machine", self.instance.machine) != self.instance.machine:
                raise serializers.ValidationError({"machine": "Create a separate product for a different machine."})
            if attrs.get("machine_slot", self.instance.machine_slot) != self.instance.machine_slot and self.instance.orders.exclude(
                status__in=["COLLECTED", "FAILED", "CANCELLED"],
            ).exists():
                raise serializers.ValidationError({"machine_slot": "Finish open orders before changing the slot."})
        return attrs


def save_record(serializer):
    serializer.is_valid(raise_exception=True)
    try:
        with transaction.atomic():
            serializer.save()
    except IntegrityError:
        raise serializers.ValidationError({"detail": "Machine code or machine slot already exists. Refresh and try again."})
    return serializer.data


@api_view(["GET", "POST"])
@permission_classes([IsAdminUser])
def machines(request):
    if request.method == "GET":
        return Response({"machines": MachineSerializer(Machine.objects.order_by("code"), many=True).data})
    return Response(save_record(MachineSerializer(data=request.data)), status=201)


@api_view(["PATCH"])
@permission_classes([IsAdminUser])
@transaction.atomic
def update_machine(request, pk):
    machine = get_object_or_404(Machine.objects.select_for_update(), pk=pk)
    return Response(save_record(MachineSerializer(machine, data=request.data, partial=True)))


@api_view(["GET", "POST"])
@permission_classes([IsAdminUser])
def products(request):
    if request.method == "GET":
        records = Product.objects.select_related("machine").order_by("machine__code", "machine_slot")
        if request.query_params.get("machine"):
            machine = get_object_or_404(Machine, code=request.query_params["machine"])
            records = records.filter(machine=machine)
        return Response({"products": ProductSerializer(records, many=True).data})
    return Response(save_record(ProductSerializer(data=request.data)), status=201)


@api_view(["PATCH"])
@permission_classes([IsAdminUser])
@transaction.atomic
def update_product(request, pk):
    product = get_object_or_404(Product.objects.select_for_update(of=("self",)).select_related("machine"), pk=pk)
    return Response(save_record(ProductSerializer(product, data=request.data, partial=True)))
