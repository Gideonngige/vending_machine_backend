from django.urls import path
from . import views

from .api_views.order import create_order, order_detail, collect_order
from .api_views.payment import initiate_coffee_payment, mpesa_callback
from .api_views.product import product_list
from .api_views.reports import sales_summary
from . import auth_views
from .api_views import management

urlpatterns = [
    path("management/machines/", management.machines),
    path("management/machines/<int:pk>/", management.update_machine),
    path("management/products/", management.products),
    path("management/products/<int:pk>/", management.update_product),
    path("auth/me/", auth_views.current_staff),
    path("auth/refresh/", auth_views.staff_refresh),
    path("auth/login/", auth_views.staff_login),
    path("auth/logout/", auth_views.staff_logout),
    path("reports/sales/", sales_summary, name="sales-summary"),
    path('', views.index, name='index'),

    path("products/", product_list, name="product-list"),

    path("orders/", create_order, name="create-order"),
    path("orders/<str:reference>/", order_detail, name="order-detail"),
    path("orders/<str:reference>/collect/", collect_order, name="collect-order"),

    path("payments/<str:reference>/stk-push/", initiate_coffee_payment, name="coffee-stk-push"),
    path("payments/callback/", mpesa_callback, name="mpesa-callback"),
]
