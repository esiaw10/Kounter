from django.contrib import admin

from .models import (
    Category,
    Product,
    Sale,
    StockMovement,
    Store,
    User,
    Subscription,
)


@admin.register(Store)
class StoreAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "phone",
        "address",
        "active",
        "created_at",
    )
    list_filter = ("active",)
    search_fields = (
        "name",
        "phone",
        "address",
    )
    readonly_fields = ("created_at",)


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = (
        "username",
        "first_name",
        "last_name",
        "email",
        "role",
        "store",
        "is_active",
        "is_staff",
    )
    list_filter = (
        "role",
        "store",
        "is_active",
        "is_staff",
    )
    search_fields = (
        "username",
        "first_name",
        "last_name",
        "email",
    )


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "store",
        "category",
        "selling_price",
        "cost_price",
        "stock",
        "low_stock_threshold",
        "active",
    )
    list_filter = (
        "store",
        "category",
        "active",
    )
    search_fields = (
        "name",
        "barcode",
    )
    list_select_related = (
        "store",
        "category",
    )


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "store",
        "quantity",
        "unit_price",
        "total",
        "sold_by",
        "sold_at",
    )
    list_filter = (
        "store",
        "sold_at",
    )
    search_fields = (
        "product__name",
        "sold_by__username",
    )
    readonly_fields = (
        "sold_at",
    )
    list_select_related = (
        "product",
        "store",
        "sold_by",
    )


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = (
        "product",
        "store",
        "movement_type",
        "quantity",
        "user",
        "created_at",
    )
    list_filter = (
        "store",
        "movement_type",
        "created_at",
    )
    search_fields = (
        "product__name",
        "user__username",
        "note",
    )
    readonly_fields = (
        "created_at",
    )
    list_select_related = (
        "product",
        "store",
        "user",
    )


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "store",
        "plan",
        "amount",
        "status",
        "start_date",
        "end_date",
        "payment_status",
        "payment_reference",
        "created_at",
    )

    list_filter = (
        "status",
        "plan",
        "payment_status",
    )

    search_fields = (
        "store__name",
        "payment_reference",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
    )

    list_select_related = (
        "store",
    )

    ordering = (
        "-created_at",
    )
