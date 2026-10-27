
from django.contrib import admin

from .models import Business, BusinessMembership, Machine, CoffeeProduct, MachineProduct



admin.site.register(Business)
admin.site.register(BusinessMembership)
admin.site.register(Machine)
admin.site.register(CoffeeProduct)
admin.site.register(MachineProduct)


