from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import AbstractUser

from django.db import models

from django.db.models.signals import pre_delete

from django.dispatch import receiver

from django.utils import timezone

# ============================================================

# STORE

# ============================================================


class Store(models.Model):

    name = models.CharField(

        max_length=150,

    )

    phone = models.CharField(

        max_length=30,

        blank=True,

    )

    address = models.CharField(

        max_length=255,

        blank=True,

    )

    active = models.BooleanField(

        default=True,

    )

    created_at = models.DateTimeField(

        auto_now_add=True,

    )

    class Meta:

        ordering = ["name"]

    def __str__(self):

        return self.name

    # --------------------------------------------------------

    # 30-DAY FREE TRIAL

    # --------------------------------------------------------

    @property
    def trial_end_date(self):
        """

        The date/time the store's 30-day free trial ends.

        """

        return self.created_at + timedelta(days=30)

    @property
    def trial_days_remaining(self):
        """

        Number of whole days remaining in the free trial.

        Returns 0 after the trial expires.

        """

        now = timezone.now()

        if now >= self.trial_end_date:

            return 0

        remaining = self.trial_end_date - now

        return max(0, remaining.days)

    @property
    def trial_active(self):
        """

        True while the store is within its 30-day trial.

        """

        return timezone.now() < self.trial_end_date

    # --------------------------------------------------------

    # ACTIVE SUBSCRIPTION

    # --------------------------------------------------------

    @property
    def active_subscription(self):
        """

        Return the currently active paid subscription.

        A subscription is considered active only when:

        - status is ACTIVE

        - start date has arrived

        - end date has not passed

        """

        now = timezone.now()

        return (

            self.subscriptions

            .filter(

                status="ACTIVE",

                start_date__lte=now,

                end_date__gt=now,

            )

            .order_by("-end_date")

            .first()

        )

    @property
    def subscription_active(self):
        """

        True when the store has a currently active subscription.

        """

        return self.active_subscription is not None

    # --------------------------------------------------------

    # PRODUCT MANAGEMENT ACCESS

    # --------------------------------------------------------

    @property
    def can_add_products(self):
        """

        Determines whether the store can manage products.

        Product management is allowed:

        1. During the 30-day free trial, OR

        2. While a paid subscription is active.

        After the trial expires and there is no active

        subscription, product management is blocked.

        """

        if self.trial_active:

            return True

        if self.subscription_active:

            return True

        return False

    @property
    def subscription_days_remaining(self):
        """

        Number of days remaining on the current subscription.

        Returns 0 when there is no active subscription.

        """

        subscription = self.active_subscription

        if not subscription:

            return 0

        remaining = subscription.end_date - timezone.now()

        return max(0, remaining.days)

# ============================================================

# SUBSCRIPTION

# ============================================================


class Subscription(models.Model):

    STATUS_CHOICES = [

        ("PENDING", "Pending"),

        ("ACTIVE", "Active"),

        ("EXPIRED", "Expired"),

        ("CANCELLED", "Cancelled"),

    ]

    PLAN_CHOICES = [

        ("MONTHLY", "Monthly"),

        ("QUARTERLY", "Quarterly"),

        ("YEARLY", "Yearly"),

    ]

    store = models.ForeignKey(

        Store,

        on_delete=models.CASCADE,

        related_name="subscriptions",

    )

    plan = models.CharField(

        max_length=20,

        choices=PLAN_CHOICES,

    )

    amount = models.DecimalField(

        max_digits=10,

        decimal_places=2,

    )

    start_date = models.DateTimeField(

        null=True,

        blank=True,

    )

    end_date = models.DateTimeField(

        null=True,

        blank=True,

    )

    status = models.CharField(

        max_length=20,

        choices=STATUS_CHOICES,

        default="PENDING",

    )

    payment_reference = models.CharField(

        max_length=255,

        blank=True,

        null=True,

    )

    payment_status = models.CharField(

        max_length=50,

        default="PENDING",

    )

    created_at = models.DateTimeField(

        auto_now_add=True,

    )

    updated_at = models.DateTimeField(

        auto_now=True,

    )

    class Meta:

        ordering = ["-created_at"]

        indexes = [

            models.Index(

                fields=["store", "status"]

            ),

            models.Index(

                fields=["store", "end_date"]

            ),

            models.Index(

                fields=["payment_reference"]

            ),

        ]

    def __str__(self):

        return (

            f"{self.store.name} - "

            f"{self.get_plan_display()} - "

            f"{self.get_status_display()}"

        )

    @property
    def is_active(self):
        """

        Determine whether this subscription is currently active.

        """

        if self.status != "ACTIVE":

            return False

        if not self.start_date or not self.end_date:

            return False

        now = timezone.now()

        return self.start_date <= now < self.end_date

    @property
    def days_remaining(self):
        """

        Number of days remaining on this subscription.

        """

        if not self.is_active:

            return 0

        now = timezone.now()

        remaining_seconds = (

            self.end_date - now

        ).total_seconds()

        if remaining_seconds <= 0:

            return 0

        return max(

            1,

            int(

                (remaining_seconds + 86399) // 86400

            )

        )

# ============================================================

# USER

# ============================================================


class User(AbstractUser):

    ROLE_CHOICES = [

        ("ADMIN", "Store Admin"),

        ("MANAGER", "Manager"),

        ("ATTENDANT", "Attendant"),

    ]

    role = models.CharField(

        max_length=20,

        choices=ROLE_CHOICES,

        default="ATTENDANT",

    )

    store = models.ForeignKey(

        Store,

        on_delete=models.SET_NULL,

        null=True,

        blank=True,

        related_name="users",

    )

    # Custom related names prevent clashes with Django's

    # default auth.User relationships.

    groups = models.ManyToManyField(

        "auth.Group",

        blank=True,

        related_name="core_user_set",

        related_query_name="core_user",

    )

    user_permissions = models.ManyToManyField(

        "auth.Permission",

        blank=True,

        related_name="core_user_permissions_set",

        related_query_name="core_user_permission",

    )

    class Meta:

        ordering = ["username"]

    def __str__(self):

        return self.username

# ============================================================

# CATEGORY

# ============================================================


class Category(models.Model):

    store = models.ForeignKey(

        Store,

        on_delete=models.CASCADE,

        related_name="categories",

    )

    name = models.CharField(

        max_length=100,

    )

    class Meta:

        ordering = ["name"]

        constraints = [

            models.UniqueConstraint(

                fields=["store", "name"],

                name="unique_category_per_store",

            )

        ]

    def __str__(self):

        return self.name

# ============================================================

# PRODUCT

# ============================================================


class Product(models.Model):

    store = models.ForeignKey(

        Store,

        on_delete=models.CASCADE,

        related_name="products",

    )

    category = models.ForeignKey(

        Category,

        on_delete=models.SET_NULL,

        null=True,

        blank=True,

        related_name="products",

    )

    name = models.CharField(

        max_length=200,

    )

    selling_price = models.DecimalField(

        max_digits=12,

        decimal_places=2,

    )

    cost_price = models.DecimalField(

        max_digits=12,

        decimal_places=2,

        default=0,

    )

    stock = models.PositiveIntegerField(

        default=0,

    )

    low_stock_threshold = models.PositiveIntegerField(

        default=20,

    )

    barcode = models.CharField(

        max_length=100,

        unique=True,

        blank=True,

        null=True,

    )

    active = models.BooleanField(

        default=True,

    )

    created_at = models.DateTimeField(

        auto_now_add=True,

    )

    updated_at = models.DateTimeField(

        auto_now=True,

    )

    class Meta:

        ordering = ["name"]

        indexes = [

            models.Index(

                fields=["store", "active"]

            ),

            models.Index(

                fields=["store", "name"]

            ),

            models.Index(

                fields=["store", "barcode"]

            ),

        ]

    def __str__(self):

        return self.name

    @property
    def is_low_stock(self):

        return self.stock <= self.low_stock_threshold

    @property
    def profit_per_unit(self):

        return self.selling_price - self.cost_price

# ============================================================

# SALE

# ============================================================


class Sale(models.Model):

    store = models.ForeignKey(

        Store,

        on_delete=models.CASCADE,

        related_name="sales",

    )

    product = models.ForeignKey(

        Product,

        on_delete=models.PROTECT,

        related_name="sales",

    )

    quantity = models.PositiveIntegerField(

        default=1,

    )

    unit_price = models.DecimalField(

        max_digits=12,

        decimal_places=2,

    )

    total_price = models.DecimalField(

        max_digits=12,

        decimal_places=2,

    )

    sold_by = models.ForeignKey(

        User,

        on_delete=models.SET_NULL,

        null=True,

        blank=True,

        related_name="sales_made",

    )

    # Historical seller name remains after the user account is deleted.

    # None identifies older sales whose original cost was not recorded.
    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        default=None,
    )

    sold_by_username = models.CharField(

        max_length=150,

        blank=True,

        default="",

    )

    created_at = models.DateTimeField(

        auto_now_add=True,

    )

    class Meta:

        ordering = ["-created_at"]

        indexes = [

            models.Index(

                fields=["store", "created_at"]

            ),

            models.Index(

                fields=["store", "product"]

            ),

        ]

    @property
    def cost_is_estimated(self):
        return self.unit_cost is None

    @property
    def effective_unit_cost(self):
        if self.unit_cost is not None:
            return self.unit_cost
        return self.product.cost_price

    @property
    def cost_total(self):
        return self.effective_unit_cost * self.quantity

    @property
    def gross_profit(self):
        return self.total_price - self.cost_total

    @property
    def profit_margin_display(self):
        if self.total_price <= 0:
            return "—"
        margin = self.gross_profit / self.total_price * Decimal("100")
        return f"{margin:.2f}%"

    def save(self, *args, **kwargs):
        if self._state.adding and self.unit_cost is None and self.product_id:
            self.unit_cost = self.product.cost_price
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(
                    kwargs["update_fields"]) | {"unit_cost"}

        if not self.sold_by_username and self.sold_by_id:

            self.sold_by_username = self.sold_by.username

            update_fields = kwargs.get("update_fields")

            if update_fields is not None:

                kwargs["update_fields"] = set(update_fields) | {

                    "sold_by_username"}

        return super().save(*args, **kwargs)

    def __str__(self):

        return (

            f"{self.product.name} x "

            f"{self.quantity} - "

            f"{self.total_price}"

        )

# ============================================================

# STOCK MOVEMENT

# ============================================================


class StockMovement(models.Model):

    MOVEMENT_TYPES = [

        ("SALE", "Sale"),

        ("RESTOCK", "Restock"),

        ("RESET", "Reset"),

        ("ADJUSTMENT", "Adjustment"),

    ]

    store = models.ForeignKey(

        Store,

        on_delete=models.CASCADE,

        related_name="stock_movements",

    )

    product = models.ForeignKey(

        Product,

        on_delete=models.CASCADE,

        related_name="stock_movements",

    )

    movement_type = models.CharField(

        max_length=20,

        choices=MOVEMENT_TYPES,

    )

    quantity = models.IntegerField()

    user = models.ForeignKey(

        User,

        on_delete=models.SET_NULL,

        null=True,

        blank=True,

        related_name="stock_movements",

    )

    note = models.CharField(

        max_length=255,

        blank=True,

    )

    created_at = models.DateTimeField(

        auto_now_add=True,

    )

    class Meta:

        ordering = ["-created_at"]

        indexes = [

            models.Index(

                fields=["store", "created_at"]

            ),

            models.Index(

                fields=["product", "created_at"]

            ),

        ]

    def __str__(self):

        return (

            f"{self.product.name} - "

            f"{self.movement_type} - "

            f"{self.quantity}"

        )


@receiver(pre_delete, sender=User)
def preserve_seller_name_before_user_deletion(sender, instance, using, **kwargs):
    """Capture names on older sales before SET_NULL clears their seller link."""

    Sale.objects.using(using).filter(

        sold_by_id=instance.pk,

        sold_by_username="",

    ).update(sold_by_username=instance.username)
