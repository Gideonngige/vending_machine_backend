
from django.contrib import admin

from .models import (
    Business, 
    BusinessMembership, 
    Machine, 
    CoffeeProduct, 
    MachineProduct,
    CoffeeOrder,
    CoffeePayment,
    DispenseJob,
    DarajaConfiguration,
    CoffeePricing,
)



admin.site.register(Business)
admin.site.register(BusinessMembership)
admin.site.register(Machine)
admin.site.register(CoffeeProduct)
admin.site.register(MachineProduct)
admin.site.register(CoffeeOrder)
admin.site.register(CoffeePayment)
admin.site.register(DispenseJob)
admin.site.register(DarajaConfiguration)
admin.site.register(CoffeePricing)


