from django.contrib import admin
from .models import Service, Counter, Ticket


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ("prefix", "name", "default_minutes", "is_active")


@admin.register(Counter)
class CounterAdmin(admin.ModelAdmin):
    list_display = ("name", "staff", "is_open")
    filter_horizontal = ("services",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ("id", "service", "number", "status", "counter", "created_at", "rating")
    list_filter = ("status", "service", "counter")