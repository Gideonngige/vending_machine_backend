from django.contrib import admin
from django.urls import path, include
from vending_machine_app.dashboard import sales_dashboard
from vending_machine_app.auth_views import admin_login

urlpatterns = [
    path('api/v1/', include('vending_machine_app.urls')),
    path('admin/login/', admin_login),
    path('admin/sales-report/', admin.site.admin_view(sales_dashboard), name='sales-dashboard'),
    path('admin/', admin.site.urls),
]
