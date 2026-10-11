from django.urls import path
from . import views

from .api_views.order import *
from .api_views.payment import *
from .api_views.product import *

urlpatterns = [
    path('', views.index, name='index'),

    path("products/", product_list, name="product-list"),

    path("orders/", create_order, name="create-order"),
    path("orders/<str:reference>/", order_detail, name="order-detail"),
    path("orders/<str:reference>/collect/", collect_order, name="collect-order"),

    path("payments/<str:reference>/stk-push/", initiate_coffee_payment, name="coffee-stk-push"),
    path("payments/callback/", mpesa_callback, name="mpesa-callback"),
]