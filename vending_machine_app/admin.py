
from django.contrib import admin

from .models import Product, Order, Payment, DispenseRequest


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
