from django.urls import path
from . import views

from .api_views.order import *
from .api_views.payment import *
from .api_views.product import *
from .api_views.reports import sales_summary
from . import auth_views

urlpatterns = [
    path("auth/session/", auth_views.session_status),
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
