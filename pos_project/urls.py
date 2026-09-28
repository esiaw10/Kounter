from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from core import views

urlpatterns = [
    path("admin/", admin.site.urls),

    # Authentication
    path("login/", views.custom_login, name="login"),
    path("logout/", views.custom_logout_view, name="logout"),
    path("signup/", views.signup, name="signup"),

    # Password reset
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="core/password_reset.html",
            email_template_name="core/password_reset_email.txt",
            subject_template_name="core/password_reset_subject.txt",
            success_url=reverse_lazy("password_reset_done"),
        ),
        name="password_reset",
    ),

    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="core/password_reset_done.html",
        ),
        name="password_reset_done",
    ),

    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="core/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),

    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="core/password_reset_complete.html",
        ),
        name="password_reset_complete",
    ),

    # Dashboard
    path("", views.dashboard, name="dashboard"),

    # POS
    path("pos/", views.pos, name="pos"),
    path(
        "pos/sell/<int:product_id>/",
        views.sell_one,
        name="sell_one",
    ),
    path(
        "pos/cart-sale/",
        views.process_cart_sale,
        name="process_cart_sale",
    ),

    # Products
    path(
        "products/active/",
        views.active_products,
        name="active_products",
    ),
    path(
        "products/active/reset/",
        views.reset_active_products,
        name="reset_active_products",
    ),
    path(
        "products/delete/<int:product_id>/",
        views.delete_product,
        name="delete_product",
    ),
    path(
        "add-product/",
        views.add_product,
        name="add_product",
    ),

    # Subscription
    path(
        "subscription/",
        views.subscription,
        name="subscription",
    ),
    path(
        "subscription/test-activate/",
        views.test_activate_subscription,
        name="test_activate_subscription",
    ),
    path(
        "subscription/pay/",
        views.paystack_initialize,
        name="paystack_initialize",
    ),
    path(
        "subscription/pay/callback/",
        views.paystack_callback,
        name="paystack_callback",
    ),
    path(
        "subscription/webhook/",
        views.paystack_webhook,
        name="paystack_webhook",
    ),

    # Stock and sales
    path(
        "pos/restock/",
        views.restock,
        name="restock",
    ),
    path(
        "sales/daily/",
        views.daily_sales,
        name="daily_sales",
    ),
    path(
        "sales/all/",
        views.all_sales,
        name="all_sales",
    ),

    # Store management
    path(
        "management/stores/",
        views.manage_stores,
        name="manage_stores",
    ),
    path(
        "management/stores/add/",
        views.add_store,
        name="add_store",
    ),
    path(
        "management/stores/<int:store_id>/edit/",
        views.edit_store,
        name="edit_store",
    ),
    path(
        "management/stores/<int:store_id>/delete/",
        views.delete_store,
        name="delete_store",
    ),
    path(
        "management/stores/<int:store_id>/toggle/",
        views.toggle_store,
        name="toggle_store",
    ),

    # User management
    path(
        "management/users/",
        views.manage_users,
        name="manage_users",
    ),
    path(
        "management/users/add/",
        views.add_user,
        name="add_user",
    ),
    path(
        "management/users/<int:user_id>/edit/",
        views.edit_user,
        name="edit_user",
    ),
    path(
        "management/users/<int:user_id>/toggle/",
        views.toggle_user,
        name="toggle_user",
    ),
    path(
        "management/users/<int:user_id>/delete/",
        views.delete_user,
        name="delete_user",
    ),
    path(
        "management/users/<int:user_id>/reset-password/",
        views.reset_user_password,
        name="reset_user_password",
    ),

    # Subscription management
    path(
        "management/subscriptions/",
        views.manage_subscriptions,
        name="manage_subscriptions",
    ),
    path(
        "management/subscriptions/<int:store_id>/activate/",
        views.admin_activate_subscription,
        name="admin_activate_subscription",
    ),
    path(
        "management/subscriptions/<int:subscription_id>/extend/",
        views.admin_extend_subscription,
        name="admin_extend_subscription",
    ),
    path(
        "management/subscriptions/<int:subscription_id>/cancel/",
        views.admin_cancel_subscription,
        name="admin_cancel_subscription",
    ),
    path(
        "management/subscriptions/<int:subscription_id>/expire/",
        views.admin_expire_subscription,
        name="admin_expire_subscription",
    ),

    # Profile
    path(
        "profile/",
        views.profile,
        name="profile",
    ),

]
