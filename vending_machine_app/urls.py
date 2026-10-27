from django.urls import path
from . import views
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)

from .api_views.auth import *
from .api_views.businesses import *
from .api_views.machines import *
from .api_views.catalog import *
from .api_views.storefront import *
from .api_views.checkout import *



urlpatterns = [
    path('', views.index, name='index'),

    # auth
    path("auth/register/", register, name='register'),
    path("auth/login/", TokenObtainPairView.as_view(), name='login'),
    path("auth/refresh/", TokenRefreshView.as_view(), name='refresh'),
    path("auth/me/", my_profile, name='my_profile'),

    # businesses
    path("business/", business_list_create, name='busness_list_create'),
    path("business/<int:business_id>/", business_detail, name='business_detail'),

    # machines
    path("machines/", machine_list_create, name='machine_list_create'),
    path("machines/<int:machine_id>/", machine_detail, name='machine_detail'),


    # catalog
    path("products/", product_list_create, name='product_lits_create'),
    path("products/<int:product_id>/", product_detail, name='product_detail'),
    path("machine-products/", machine_product_list_create, name='machine_product_list_create'),
    path("machine-products/<int:item_id>/", machine_product_detail, name='machine_product_detail'),

    # store front
    path("marketplace/businesses/", marketplace_businesses, name='marketplace_businesses'),
    path("storefront/machines/<uuid:machine_id>/products/", storefront_machine_products, name='storefront_machine_products'),
    path("storefront/<slug:slug>/", storefront_detail, name='storefront_detail'),

    # checkout
    path("orders/", create_order, name='create_order'),
    path("orders/<str:reference>/", order_status, name='order_status'),
    path("orders/<str:reference>/pay/", initiate_payment, name='initiate_payment'),
    path("orders/<str:reference>/collect/", collect_order, name='collect_order'),
    path("payments/mpesa/callback/", mpesa_callback, name='mpesa_callback'),
    
]
