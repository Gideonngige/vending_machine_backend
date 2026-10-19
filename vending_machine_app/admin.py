
from django.contrib import admin

from .models import Product, Order, Payment, PaymentAttempt, DispenseRequest


@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    list_display = ("payment", "status", "amount", "checkout_request_id", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("payment__order__reference", "checkout_request_id", "mpesa_receipt_number")

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "price",
        "machine_slot",
        "stock_quantity",
        "is_available",
    )
    list_filter = ("is_available",)
    search_fields = ("name",)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        "reference",
        "product_name",
        "customer_phone",
        "total_amount",
        "status",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = ("reference", "customer_phone", "product_name")
    readonly_fields = ("reference", "created_at", "updated_at")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "order",
        "phone_number",
        "amount",
        "status",
        "mpesa_receipt_number",
        "created_at",
    )
    list_filter = ("status", "created_at")
    search_fields = (
        "order__reference",
        "phone_number",
        "mpesa_receipt_number",
    )
    readonly_fields = (
        "merchant_request_id",
        "checkout_request_id",
        "mpesa_receipt_number",
        "result_code",
        "result_description",
        "paid_at",
    )


@admin.register(DispenseRequest)
class DispenseRequestAdmin(admin.ModelAdmin):
    list_display = (
        "order",
        "machine_slot",
        "status",
        "created_at",
        "completed_at",
    )
    list_filter = ("status",)
    search_fields = ("order__reference",)
