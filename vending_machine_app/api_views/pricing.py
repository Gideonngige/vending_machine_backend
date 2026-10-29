from .common_imports import *



# =====================================================
# PERMISSIONS
# =====================================================

def get_membership(user, business_id, manage=False):
    queryset = BusinessMembership.objects.filter(
        user=user,
        business_id=business_id,
        is_active=True,
    )

    if manage:
        queryset = queryset.filter(
            role__in=["OWNER", "MANAGER"]
        )

    return queryset.first()


def pricing_queryset(business_id):
    return (
        CoffeePricing.objects
        .filter(business_id=business_id)
        .select_related("business", "machine", "product")
    )


# =====================================================
# SERIALIZERS
# =====================================================

class CoffeePricingSerializer(serializers.ModelSerializer):
    machine_name = serializers.CharField(
        source="machine.name",
        read_only=True,
    )
    machine_code = serializers.CharField(
        source="machine.machine_code",
        read_only=True,
    )
    product_name = serializers.CharField(
        source="product.name",
        read_only=True,
    )
    base_price = serializers.DecimalField(
        source="product.base_price",
        max_digits=10,
        decimal_places=2,
        read_only=True,
    )

    class Meta:
        model = CoffeePricing
        fields = [
            "id",
            "business",
            "machine",
            "machine_name",
            "machine_code",
            "product",
            "product_name",
            "base_price",
            "price",
            "is_active",
        ]
        read_only_fields = ["business"]

    def validate_price(self, value):
        if value < Decimal("0.00"):
            raise serializers.ValidationError(
                "Price cannot be negative."
            )
        return value

    def validate(self, attrs):
        business_id = self.context["business_id"]

        machine = attrs.get(
            "machine",
            getattr(self.instance, "machine", None),
        )

        product = attrs.get(
            "product",
            getattr(self.instance, "product", None),
        )

        if machine is None or product is None:
            raise serializers.ValidationError(
                "Machine and product are required."
            )

        if machine.business_id != int(business_id):
            raise serializers.ValidationError({
                "machine": "Machine does not belong to this business."
            })

        if product.business_id != int(business_id):
            raise serializers.ValidationError({
                "product": "Product does not belong to this business."
            })

        existing = CoffeePricing.objects.filter(
            business_id=business_id,
            machine=machine,
            product=product,
        )

        if self.instance:
            existing = existing.exclude(pk=self.instance.pk)

        if existing.exists():
            raise serializers.ValidationError({
                "product": (
                    "A pricing rule already exists for "
                    "this product on this machine."
                )
            })

        return attrs


# =====================================================
# LIST / CREATE
# =====================================================

@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def pricing_list_create(request):
    if request.method == "GET":
        business_id = request.query_params.get("business")

        if not business_id:
            return Response(
                {"detail": "Business ID is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not get_membership(request.user, business_id):
            return Response(
                {"detail": "Business access denied."},
                status=status.HTTP_403_FORBIDDEN,
            )

        queryset = pricing_queryset(business_id).order_by(
            "machine__name",
            "product__name",
        )

        machine_id = request.query_params.get("machine")

        if machine_id:
            queryset = queryset.filter(machine_id=machine_id)

        serializer = CoffeePricingSerializer(
            queryset,
            many=True,
        )

        return Response({
            "count": queryset.count(),
            "pricing": serializer.data,
        })

    business_id = request.data.get("business")

    if not business_id:
        return Response(
            {"detail": "Business ID is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not get_membership(
        request.user,
        business_id,
        manage=True,
    ):
        return Response(
            {"detail": "Owner or manager access required."},
            status=status.HTTP_403_FORBIDDEN,
        )

    serializer = CoffeePricingSerializer(
        data=request.data,
        context={"business_id": business_id},
    )
    serializer.is_valid(raise_exception=True)

    pricing = serializer.save(business_id=business_id)

    return Response(
        CoffeePricingSerializer(pricing).data,
        status=status.HTTP_201_CREATED,
    )


# =====================================================
# DETAIL / UPDATE / DELETE
# =====================================================

@api_view(["GET", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def pricing_detail(request, pricing_id):
    # Always scope the lookup to businesses the user
    # is authorized to access.
    membership_query = BusinessMembership.objects.filter(
        user=request.user,
        is_active=True,
    )

    if request.method != "GET":
        membership_query = membership_query.filter(
            role__in=["OWNER", "MANAGER"]
        )

    allowed_business_ids = membership_query.values_list(
        "business_id",
        flat=True,
    )

    pricing = get_object_or_404(
        CoffeePricing.objects.select_related(
            "machine",
            "product",
        ).filter(business_id__in=allowed_business_ids),
        pk=pricing_id,
    )

    if request.method == "GET":
        return Response(
            CoffeePricingSerializer(pricing).data
        )

    if request.method == "DELETE":
        pricing.delete()
        return Response(
            status=status.HTTP_204_NO_CONTENT
        )

    serializer = CoffeePricingSerializer(
        pricing,
        data=request.data,
        partial=True,
        context={"business_id": pricing.business_id},
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()

    return Response(serializer.data)


# =====================================================
# PRICING STATISTICS
# =====================================================

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def pricing_stats(request):
    business_id = request.query_params.get("business")

    if not business_id:
        return Response(
            {"detail": "Business ID is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not get_membership(request.user, business_id):
        return Response(
            {"detail": "Business access denied."},
            status=status.HTTP_403_FORBIDDEN,
        )

    queryset = pricing_queryset(business_id)

    aggregates = queryset.aggregate(
        average_price=Avg("price"),
        lowest_price=Min("price"),
        highest_price=Max("price"),
    )

    return Response({
        "total_rules": queryset.count(),
        "active_rules": queryset.filter(
            is_active=True
        ).count(),
        "machines_with_pricing": queryset.values(
            "machine_id"
        ).distinct().count(),
        "average_price": str(
            aggregates["average_price"] or 0
        ),
        "lowest_price": str(
            aggregates["lowest_price"] or 0
        ),
        "highest_price": str(
            aggregates["highest_price"] or 0
        ),
        "currency": "KES",
    })
