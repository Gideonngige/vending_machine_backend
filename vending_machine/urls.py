from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('api/v1/', include('vending_machine_app.urls')),
    path('admin/', admin.site.urls),
]
