from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
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

    # ========================================================
    # FREE TRIAL
    # ========================================================

    @property
    def trial_end_date(self):
        """
        The free trial ends 30 days after the store was created.
        """
        return self.created_at + timedelta(days=30)

    @property
    def trial_days_remaining(self):
        """
        Number of days remaining in the free trial.
        Returns 0 when the trial has expired.
        """

        now = timezone.now()

        if now >= self.trial_end_date:
            return 0

        remaining = self.trial_end_date - now

        return max(0, remaining.days)

    @property
    def trial_active(self):
        """
        Returns True while the 30-day free trial is active.
        """

        return timezone.now() < self.trial_end_date

    # ========================================================
    # SUBSCRIPTION
    # ========================================================

    @property
    def active_subscription(self):
        """
        Returns the currently active subscription, if one exists.
        """

        now = timezone.now()

        return self.subscriptions.filter(
            status="ACTIVE",
            start_date__lte=now,
            end_date__gt=now,
        ).order_by("-end_date").first()

    @property
    def subscription_active(self):
        """
        Returns True when the store has an active paid subscription.
        """

        return self.active_subscription is not None

    @property
    def can_add_products(self):
        """
        Determines whether this store is currently allowed
        to add new products.

        Product creation is allowed when:

        1. The 30-day free trial is still active, OR
        2. The store has an active paid subscription.
        """

        if self.trial_active:
            return True

        if self.subscription_active:
            return True

        return False

    @property
    def subscription_days_remaining(self):
        """
        Returns the number of days remaining on the active
        subscription.

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
                fields=["store", "status"],
            ),
            models.Index(
                fields=["store", "end_date"],
            ),
            models.Index(
                fields=["payment_reference"],
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
        Checks whether this subscription is currently valid.
        """

        if self.status != "ACTIVE":
            return False

        if not self.start_date or not self.end_date:
            return False

        now = timezone.now()

        return self.start_date <= now < self.end_date

    @property
    def days_remaining(self):
        if not self.is_active:
            return 0

        now = timezone.now()

        # Count the current day as a full subscription day.
        remaining_seconds = (self.end_date - now).total_seconds()

        if remaining_seconds <= 0:
            return 0

        return max(1, int((remaining_seconds + 86399) // 86400))


# ============================================================
# USER
# ============================================================

class User(AbstractUser):

    ROLE_CHOICES = [
        ("ADMIN", "Administrator"),
        ("MANAGER", "Store Manager"),
        ("ATTENDANT", "Attendant"),
    ]

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default="ATTENDANT",
    )

    store = models.ForeignKey(
        Store,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="users",
    )

    groups = models.ManyToManyField(
        "auth.Group",
        related_name="core_user_set",
        blank=True,
        help_text="The groups this user belongs to.",
        verbose_name="groups",
    )

    user_permissions = models.ManyToManyField(
        "auth.Permission",
        related_name="core_user_permissions_set",
        blank=True,
        help_text="Specific user permissions.",
        verbose_name="user permissions",
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
        max_digits=10,
        decimal_places=2,
    )

    cost_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    stock = models.PositiveIntegerField(
        default=0,
    )

    low_stock_threshold = models.PositiveIntegerField(
        default=20,
    )

    barcode = models.CharField(
        max_length=100,
        blank=True,
        null=True,
    )

    active = models.BooleanField(
        default=True,
    )

    class Meta:
        ordering = ["name"]

        indexes = [
            models.Index(
                fields=["store", "active"],
            ),
            models.Index(
                fields=["store", "name"],
            ),
            models.Index(
                fields=["store", "barcode"],
            ),
        ]

    @property
    def is_low_stock(self):
        return self.stock <= self.low_stock_threshold

    def __str__(self):
        return self.name


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

    quantity = models.PositiveIntegerField()

    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
    )

    sold_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="sales",
    )

    sold_at = models.DateTimeField(
        default=timezone.now,
    )

    class Meta:
        ordering = ["-sold_at"]

        indexes = [
            models.Index(
                fields=["store", "sold_at"],
            ),
            models.Index(
                fields=["store", "product"],
            ),
        ]

    @property
    def total(self):
        return self.quantity * self.unit_price

    def __str__(self):
        return (
            f"{self.product.name} - "
            f"{self.quantity} - "
            f"{self.total}"
        )


# ============================================================
# STOCK MOVEMENT
# ============================================================

class StockMovement(models.Model):

    MOVEMENT_TYPES = (
        ("SALE", "Sale"),
        ("RESTOCK", "Restock"),
        ("ADJUSTMENT", "Adjustment"),
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="stock_movements",
    )

    store = models.ForeignKey(
        Store,
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

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    note = models.CharField(
        max_length=255,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]

        indexes = [
            models.Index(
                fields=["store", "created_at"],
            ),
            models.Index(
                fields=["store", "movement_type"],
            ),
            models.Index(
                fields=["product", "created_at"],
            ),
        ]

    def __str__(self):
        return (
            f"{self.product.name} - "
            f"{self.movement_type} - "
            f"{self.quantity}"
        )
