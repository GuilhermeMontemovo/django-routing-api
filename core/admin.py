from django.contrib import admin
from django.contrib.gis.admin import GISModelAdmin
from .models import FuelStation


@admin.register(FuelStation)
class FuelStationAdmin(GISModelAdmin):
    list_display = ('name', 'city', 'state', 'retail_price')
    list_filter = ('state',)
    search_fields = ('name', 'city', 'address')
