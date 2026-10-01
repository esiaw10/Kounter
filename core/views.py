import datetime
import hashlib
import hmac
import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from datetime import timedelta

import requests

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import (
    authenticate,
    get_user_model,
    login as auth_login,
    logout as auth_logout,
    update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import (
    AuthenticationForm,
    PasswordChangeForm,
)
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .forms import ProductForm
from .models import (
    Category,
    Product,
    Sale,
    StockMovement,
    Store,
    Subscription,
    User,
)


logger = logging.getLogger(__name__)

User = get_user_model()


# ============================================================
# PAYSTACK CONFIGURATION
# ============================================================

PAYSTACK_INITIALIZE_URL = (
    "https://api.paystack.co/transaction/initialize"
)

PAYSTACK_VERIFY_URL = (
    "https://api.paystack.co/transaction/verify/{}"
)


# ============================================================
# SUBSCRIPTION PLANS
# ============================================================

SUBSCRIPTION_PLANS = {
    "MONTHLY": {
        "amount": Decimal("50.00"),
        "days": 30,
        "name": "Monthly",
    },
    "QUARTERLY": {
        "amount": Decimal("135.00"),
        "days": 90,
        "name": "Quarterly",
    },
    "YEARLY": {
        "amount": Decimal("500.00"),
        "days": 365,
        "name": "Yearly",
    },
}


# ============================================================
# ROLE HELPERS
# ============================================================

def is_main_admin(user):
    """
    The main Super Admin of the entire POS system.
    """
    return (
        user.is_authenticated
        and user.is_superuser
    )


def is_store_admin(user):
    """
    Store ADMIN role.
    """
    return (
        user.is_authenticated
        and not user.is_superuser
        and user.role == "ADMIN"
    )


def is_manager(user):
    """
    Store Manager role.
    """
    return (
        user.is_authenticated
        and not user.is_superuser
        and user.role == "MANAGER"
    )


def is_attendant(user):
    """
    Store Attendant role.
    """
    return (
        user.is_authenticated
        and not user.is_superuser
        and user.role == "ATTENDANT"
    )


def can_manage_products(user):
    """
    Users allowed to perform product-management actions.
    """
    return (
        user.is_authenticated
        and (
            user.is_superuser
            or user.role in ["ADMIN", "MANAGER"]
        )
    )


def can_manage_stock(user):
    """
    Users allowed to restock products.
    """
    return (
        user.is_authenticated
        and (
            user.is_superuser
            or user.role in ["ADMIN", "MANAGER"]
        )
    )


def can_reset_data(user):
    """
    Users allowed to reset products and sales.
    """
    return (
        user.is_authenticated
        and (
            user.is_superuser
            or user.role in ["ADMIN", "MANAGER"]
        )
    )


def can_manage_users(user):
    """
    Only the main Super Admin manages users.
    """
    return (
        user.is_authenticated
        and user.is_superuser
    )


def can_manage_stores(user):
    """
    Only the main Super Admin manages stores.
    """
    return (
        user.is_authenticated
        and user.is_superuser
    )


# ============================================================
# STORE HELPER
# ============================================================

def get_current_store(request):
    """
    Returns the logged-in user's active store.

    Super Admin may not have a store because the Super Admin
    manages the whole POS system.
    """

    try:
        store = request.user.store
    except ObjectDoesNotExist:
        return None

    if store is None:
        return None

    if not store.active:
        return None

    return store


# ============================================================
# SUBSCRIPTION HELPERS
# ============================================================

def has_product_management_access(store):
    """Return True when product management is currently allowed."""
    if not store:
        return False

    # Store.can_add_products is the single source of truth in models.py.
    # It allows product management during the 30-day trial or an active
    # paid subscription, and blocks it after both have expired.
    return bool(store.can_add_products)


def can_add_products(store):
    """Backward-compatible alias for older code/templates."""
    return has_product_management_access(store)


def subscription_status(store):
    """Return the current trial/subscription status."""

    if not store:
        return {
            "trial_active": False,
            "subscription_active": False,
            "can_add_products": False,
            "can_manage_products": False,
            "days_remaining": 0,
            "status": "NO_STORE",
            "subscription": None,
        }

    if store.subscription_active:
        subscription = store.active_subscription

        return {
            "trial_active": False,
            "subscription_active": True,
            "can_add_products": True,
            "can_manage_products": True,
            "days_remaining": subscription.days_remaining,
            "status": "ACTIVE",
            "subscription": subscription,
        }

    if store.trial_active:
        return {
            "trial_active": True,
            "subscription_active": False,
            "can_add_products": True,
            "can_manage_products": True,
            "days_remaining": store.trial_days_remaining,
            "status": "TRIAL",
            "subscription": None,
        }

    return {
        "trial_active": False,
        "subscription_active": False,
        "can_add_products": False,
        "can_manage_products": False,
        "days_remaining": 0,
        "status": "EXPIRED",
        "subscription": None,
    }


# ============================================================
# LOGIN
# ============================================================

class AttendantAuthenticationForm(AuthenticationForm):
    """Explain deactivation only after the attendant's credentials are verified."""

    deactivated_message = "Your account has been deactivated by your manager."

    def get_invalid_login_error(self):
        username = self.cleaned_data.get("username")
        password = self.cleaned_data.get("password")
        if username and password:
            user = User.objects.filter(
                **{User.USERNAME_FIELD: username},
                role="ATTENDANT",
                is_active=False,
                is_staff=False,
                is_superuser=False,
            ).first()
            if user is not None and user.check_password(password):
                return ValidationError(self.deactivated_message, code="inactive")
        return super().get_invalid_login_error()

    def confirm_login_allowed(self, user):
        if (
            not user.is_active
            and user.role == "ATTENDANT"
            and not user.is_staff
            and not user.is_superuser
        ):
            raise ValidationError(self.deactivated_message, code="inactive")
        return super().confirm_login_allowed(user)


def custom_login(request):
    """
    Custom Kounter login view with Remember Me support.

    Remember Me keeps the session alive for 30 days. Without it,
    the session expires when the browser is closed.
    """

    if request.user.is_authenticated:
        return redirect("dashboard")

    next_url = request.POST.get("next") or request.GET.get("next", "")

    if request.method == "POST":
        form = AttendantAuthenticationForm(request, data=request.POST)

        if form.is_valid():
            user = form.get_user()
            auth_login(request, user)

            if request.POST.get("remember_me"):
                request.session.set_expiry(60 * 60 * 24 * 30)
            else:
                request.session.set_expiry(0)

            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)

            return redirect("dashboard")
    else:
        form = AttendantAuthenticationForm(request)

    return render(
        request,
        "core/login.html",
        {
            "form": form,
            "next": next_url,
        },
    )


# ============================================================
# SIGNUP
# ============================================================

def signup(request):

    if request.method == "POST":

        store_name = request.POST.get(
            "store_name",
            "",
        ).strip()

        username = request.POST.get(
            "username",
            "",
        ).strip()

        email = request.POST.get(
            "email",
            "",
        ).strip()

        password = request.POST.get(
            "password",
            "",
        )

        confirm_password = request.POST.get(
            "confirm_password",
            "",
        )

        # ----------------------------------------------------
        # VALIDATION
        # ----------------------------------------------------

        if not store_name:
            messages.error(
                request,
                "Please enter your shop name.",
            )
            return render(
                request,
                "core/signup.html",
            )

        if not username:
            messages.error(
                request,
                "Please enter a username.",
            )
            return render(
                request,
                "core/signup.html",
            )

        if not password:
            messages.error(
                request,
                "Please enter a password.",
            )
            return render(
                request,
                "core/signup.html",
            )

        if password != confirm_password:
            messages.error(
                request,
                "Passwords do not match.",
            )
            return render(
                request,
                "core/signup.html",
            )

        if User.objects.filter(
            username=username
        ).exists():

            messages.error(
                request,
                (
                    "That username is already in use. "
                    "Please choose another username."
                ),
            )

            return render(
                request,
                "core/signup.html",
            )

        # ----------------------------------------------------
        # CREATE STORE + USER
        # ----------------------------------------------------

        try:

            with transaction.atomic():

                store = Store.objects.create(
                    name=store_name,
                    active=True,
                )

                User.objects.create_user(
                    username=username,
                    email=email,
                    password=password,
                    role="ADMIN",
                    store=store,
                )

        except Exception as exc:

            logger.exception(
                "Signup failed: %s",
                exc,
            )

            messages.error(
                request,
                (
                    "Unable to create your account. "
                    "Please try again."
                ),
            )

            return render(
                request,
                "core/signup.html",
            )

        messages.success(
            request,
            (
                "Your shop account has been created successfully. "
                "Please log in using your new credentials."
            ),
        )

        return redirect("login")

    return render(
        request,
        "core/signup.html",
    )


# ============================================================
# PAYSTACK HELPERS
# ============================================================

def paystack_headers():
    """
    Returns headers required for Paystack API requests.
    """

    return {
        "Authorization": (
            f"Bearer {settings.PAYSTACK_SECRET_KEY}"
        ),
        "Content-Type": "application/json",
    }


def subscription_amount_in_subunit(amount):
    """
    Converts a GHS amount to Paystack's currency subunit.

    Example:
        GH₵50.00 -> 5000
    """

    return int(
        amount * Decimal("100")
    )


# ============================================================
# PAYSTACK - INITIALIZE SUBSCRIPTION
# ============================================================

@login_required
@require_POST
def paystack_initialize(request):

    store = get_current_store(request)

    if not store:

        messages.error(
            request,
            "You are not assigned to an active store.",
        )

        return redirect("dashboard")

    # --------------------------------------------------------
    # PERMISSION
    # --------------------------------------------------------

    if not (
        request.user.is_superuser
        or request.user.role in ["ADMIN", "MANAGER"]
    ):

        messages.error(
            request,
            (
                "You do not have permission to "
                "manage subscriptions."
            ),
        )

        return redirect("subscription")

    # --------------------------------------------------------
    # PAYSTACK SECRET KEY CHECK
    # --------------------------------------------------------

    if not settings.PAYSTACK_SECRET_KEY:

        logger.error(
            "PAYSTACK_SECRET_KEY is missing."
        )

        messages.error(
            request,
            (
                "Payment service is not configured. "
                "Please contact the administrator."
            ),
        )

        return redirect("subscription")

    # --------------------------------------------------------
    # GET PLAN
    # --------------------------------------------------------

    plan = request.POST.get(
        "plan",
        "",
    ).strip().upper()

    selected_plan = SUBSCRIPTION_PLANS.get(plan)

    if not selected_plan:

        messages.error(
            request,
            "Invalid subscription plan.",
        )

        return redirect("subscription")

    # --------------------------------------------------------
    # PREVENT DUPLICATE ACTIVE SUBSCRIPTION
    # --------------------------------------------------------

    if store.subscription_active:

        messages.warning(
            request,
            "Your store already has an active subscription.",
        )

        return redirect("subscription")

    # --------------------------------------------------------
    # EMAIL REQUIRED BY PAYSTACK
    # --------------------------------------------------------

    email = (
        request.user.email
        or ""
    ).strip()

    if not email:

        messages.error(
            request,
            (
                "Please add an email address to your profile "
                "before making a subscription payment."
            ),
        )

        return redirect("profile")

    # --------------------------------------------------------
    # CANCEL OLD PENDING PAYMENTS FOR THIS STORE
    #
    # This prevents a customer from having several active
    # checkout attempts at the same time.
    # --------------------------------------------------------

    Subscription.objects.filter(
        store=store,
        status="PENDING",
    ).update(
        status="CANCELLED",
        payment_status="CANCELLED",
    )

    # --------------------------------------------------------
    # CREATE UNIQUE PAYMENT REFERENCE
    # --------------------------------------------------------

    reference = (
        f"KTR-{uuid.uuid4().hex[:20].upper()}"
    )

    # --------------------------------------------------------
    # CREATE LOCAL PENDING SUBSCRIPTION
    # --------------------------------------------------------

    subscription_record = Subscription.objects.create(
        store=store,
        plan=plan,
        amount=selected_plan["amount"],
        status="PENDING",
        payment_reference=reference,
        payment_status="PENDING",
    )

    # --------------------------------------------------------
    # AMOUNT
    # --------------------------------------------------------

    amount_in_subunit = (
        subscription_amount_in_subunit(
            selected_plan["amount"]
        )
    )

    # --------------------------------------------------------
    # CALLBACK URL
    # --------------------------------------------------------

    callback_url = request.build_absolute_uri(
        reverse("paystack_callback")
    )

    # --------------------------------------------------------
    # PAYSTACK PAYLOAD
    # --------------------------------------------------------

    payload = {
        "email": email,
        "amount": str(amount_in_subunit),
        "currency": "GHS",
        "reference": reference,
        "callback_url": callback_url,
        "metadata": json.dumps({
            "store_id": store.id,
            "subscription_id": subscription_record.id,
            "plan": plan,
        }),
    }

    # --------------------------------------------------------
    # SEND TO PAYSTACK
    # --------------------------------------------------------

    try:

        response = requests.post(
            PAYSTACK_INITIALIZE_URL,
            json=payload,
            headers=paystack_headers(),
            timeout=30,
        )

        try:
            response_data = response.json()
        except ValueError:
            response_data = {}

    except requests.RequestException:

        logger.exception(
            "Paystack initialization request failed."
        )

        subscription_record.payment_status = "FAILED"

        subscription_record.save(
            update_fields=[
                "payment_status",
                "updated_at",
            ]
        )

        messages.error(
            request,
            (
                "Unable to connect to Paystack right now. "
                "Please try again."
            ),
        )

        return redirect("subscription")

    # --------------------------------------------------------
    # PAYSTACK ERROR
    # --------------------------------------------------------

    if (
        not response.ok
        or not response_data.get("status")
    ):

        logger.error(
            "Paystack initialization failed. "
            "HTTP=%s RESPONSE=%s",
            response.status_code,
            response_data,
        )

        subscription_record.payment_status = "FAILED"

        subscription_record.save(
            update_fields=[
                "payment_status",
                "updated_at",
            ]
        )

        messages.error(
            request,
            response_data.get(
                "message",
                "Unable to initialize Paystack payment.",
            ),
        )

        return redirect("subscription")

    # --------------------------------------------------------
    # GET AUTHORIZATION URL
    # --------------------------------------------------------

    authorization_url = (
        response_data
        .get("data", {})
        .get("authorization_url")
    )

    if not authorization_url:

        logger.error(
            "Paystack did not return authorization URL. "
            "Response=%s",
            response_data,
        )

        subscription_record.payment_status = "FAILED"

        subscription_record.save(
            update_fields=[
                "payment_status",
                "updated_at",
            ]
        )

        messages.error(
            request,
            "Paystack did not provide a payment URL.",
        )

        return redirect("subscription")

    # --------------------------------------------------------
    # REDIRECT TO PAYSTACK
    # --------------------------------------------------------

    return redirect(
        authorization_url
    )


# ============================================================
# PAYSTACK - VERIFY AND ACTIVATE
# ============================================================

@require_GET
def paystack_callback(request):

    reference = (
        request.GET.get(
            "reference",
            "",
        ).strip()
    )

    if not reference:

        messages.error(
            request,
            "No Paystack payment reference was provided.",
        )

        return redirect("login")

    # --------------------------------------------------------
    # FIND LOCAL SUBSCRIPTION
    # --------------------------------------------------------

    subscription_record = (
        Subscription.objects
        .select_related("store")
        .filter(
            payment_reference=reference,
        )
        .first()
    )

    if not subscription_record:

        logger.warning(
            "Unknown Paystack reference received: %s",
            reference,
        )

        messages.error(
            request,
            "This payment could not be found.",
        )

        return redirect("login")

    # --------------------------------------------------------
    # ALREADY ACTIVATED
    # --------------------------------------------------------

    if subscription_record.status == "ACTIVE":

        if request.user.is_authenticated:
            messages.info(
                request,
                "This payment has already been processed.",
            )
            return redirect("subscription")

        return redirect("login")

    # --------------------------------------------------------
    # ONLY PENDING PAYMENTS CAN BE ACTIVATED
    # --------------------------------------------------------

    if subscription_record.status != "PENDING":

        logger.warning(
            "Paystack callback received for non-pending "
            "subscription. ID=%s STATUS=%s",
            subscription_record.id,
            subscription_record.status,
        )

        if request.user.is_authenticated:
            messages.error(
                request,
                "This payment is no longer available for processing.",
            )
            return redirect("subscription")

        return redirect("login")

    # --------------------------------------------------------
    # SECRET KEY CHECK
    # --------------------------------------------------------

    if not settings.PAYSTACK_SECRET_KEY:

        logger.error(
            "PAYSTACK_SECRET_KEY is missing."
        )

        messages.error(
            request,
            "Payment verification is not configured.",
        )

        return redirect("login")

    # --------------------------------------------------------
    # VERIFY WITH PAYSTACK
    # --------------------------------------------------------

    verify_url = PAYSTACK_VERIFY_URL.format(
        reference
    )

    try:

        response = requests.get(
            verify_url,
            headers=paystack_headers(),
            timeout=30,
        )

        try:
            response_data = response.json()
        except ValueError:
            response_data = {}

    except requests.RequestException:

        logger.exception(
            "Paystack verification request failed."
        )

        messages.error(
            request,
            (
                "We could not verify your payment with "
                "Paystack. Please try again."
            ),
        )

        if request.user.is_authenticated:
            return redirect("subscription")

        return redirect("login")

    # --------------------------------------------------------
    # PAYSTACK RESPONSE MUST BE VALID
    # --------------------------------------------------------

    if (
        not response.ok
        or not response_data.get("status")
    ):

        logger.error(
            "Paystack verification failed. "
            "HTTP=%s RESPONSE=%s",
            response.status_code,
            response_data,
        )

        messages.error(
            request,
            "Paystack could not verify this transaction.",
        )

        if request.user.is_authenticated:
            return redirect("subscription")

        return redirect("login")

    payment_data = (
        response_data.get("data")
        or {}
    )

    # --------------------------------------------------------
    # VERIFY PAYMENT STATUS
    # --------------------------------------------------------

    payment_status = (
        payment_data.get("status")
        or ""
    ).lower()

    if payment_status != "success":

        subscription_record.payment_status = (
            payment_status.upper()
            if payment_status
            else "FAILED"
        )

        subscription_record.save(
            update_fields=[
                "payment_status",
                "updated_at",
            ]
        )

        messages.error(
            request,
            (
                "The payment was not successful. "
                "Your subscription has not been activated."
            ),
        )

        if request.user.is_authenticated:
            return redirect("subscription")

        return redirect("login")

    # --------------------------------------------------------
    # VERIFY REFERENCE
    # --------------------------------------------------------

    returned_reference = (
        payment_data.get("reference")
        or ""
    ).strip()

    if not hmac.compare_digest(
        returned_reference,
        reference,
    ):

        logger.error(
            "Paystack reference mismatch. "
            "Expected=%s Received=%s",
            reference,
            returned_reference,
        )

        subscription_record.payment_status = (
            "REFERENCE_MISMATCH"
        )

        subscription_record.save(
            update_fields=[
                "payment_status",
                "updated_at",
            ]
        )

        messages.error(
            request,
            "Payment verification failed.",
        )

        if request.user.is_authenticated:
            return redirect("subscription")

        return redirect("login")

    # --------------------------------------------------------
    # VERIFY CURRENCY
    # --------------------------------------------------------

    currency = (
        payment_data.get("currency")
        or ""
    ).upper()

    if currency != "GHS":

        logger.error(
            "Paystack currency mismatch. "
            "Expected=GHS Received=%s",
            currency,
        )

        subscription_record.payment_status = (
            "CURRENCY_MISMATCH"
        )

        subscription_record.save(
            update_fields=[
                "payment_status",
                "updated_at",
            ]
        )

        messages.error(
            request,
            "Payment currency could not be verified.",
        )

        if request.user.is_authenticated:
            return redirect("subscription")

        return redirect("login")

    # --------------------------------------------------------
    # VERIFY AMOUNT
    # --------------------------------------------------------

    expected_amount = (
        subscription_amount_in_subunit(
            subscription_record.amount
        )
    )

    paid_amount = payment_data.get(
        "amount"
    )

    if paid_amount != expected_amount:

        logger.error(
            "Paystack amount mismatch. "
            "Expected=%s Received=%s Reference=%s",
            expected_amount,
            paid_amount,
            reference,
        )

        subscription_record.payment_status = (
            "AMOUNT_MISMATCH"
        )

        subscription_record.save(
            update_fields=[
                "payment_status",
                "updated_at",
            ]
        )

        messages.error(
            request,
            (
                "The payment amount could not be verified. "
                "Please contact support."
            ),
        )

        if request.user.is_authenticated:
            return redirect("subscription")

        return redirect("login")

    # --------------------------------------------------------
    # GET PLAN DURATION
    # --------------------------------------------------------

    plan_data = SUBSCRIPTION_PLANS.get(
        subscription_record.plan
    )

    if not plan_data:

        logger.error(
            "Invalid subscription plan on payment. "
            "Subscription=%s Plan=%s",
            subscription_record.id,
            subscription_record.plan,
        )

        messages.error(
            request,
            "Invalid subscription plan.",
        )

        if request.user.is_authenticated:
            return redirect("subscription")

        return redirect("login")

    # --------------------------------------------------------
    # ACTIVATE INSIDE DATABASE TRANSACTION
    # --------------------------------------------------------

    with transaction.atomic():

        locked_subscription = (
            Subscription.objects
            .select_for_update()
            .select_related("store")
            .get(
                pk=subscription_record.pk
            )
        )

        # -----------------------------------------------
        # DOUBLE-ACTIVATION PROTECTION
        # -----------------------------------------------

        if locked_subscription.status == "ACTIVE":

            messages.info(
                request,
                "This payment has already been processed.",
            )

            if request.user.is_authenticated:
                return redirect("subscription")

            return redirect("login")

        if locked_subscription.status != "PENDING":

            messages.error(
                request,
                "This payment is no longer pending.",
            )

            if request.user.is_authenticated:
                return redirect("subscription")

            return redirect("login")

        # -----------------------------------------------
        # DETERMINE START DATE
        # -----------------------------------------------

        now = timezone.now()

        existing_active = (
            locked_subscription.store.active_subscription
        )

        if existing_active:
            start_date = existing_active.end_date
        else:
            start_date = now

        end_date = (
            start_date
            + timedelta(
                days=plan_data["days"]
            )
        )

        # -----------------------------------------------
        # ACTIVATE
        # -----------------------------------------------

        locked_subscription.start_date = start_date
        locked_subscription.end_date = end_date
        locked_subscription.status = "ACTIVE"
        locked_subscription.payment_status = "PAID"

        locked_subscription.save(
            update_fields=[
                "start_date",
                "end_date",
                "status",
                "payment_status",
                "updated_at",
            ]
        )

    # --------------------------------------------------------
    # SUCCESS
    # --------------------------------------------------------

    messages.success(
        request,
        (
            "Payment successful! "
            f"Your {plan_data['name']} subscription "
            "is now active."
        ),
    )

    if request.user.is_authenticated:
        return redirect("subscription")

    return redirect("login")


# ============================================================
# PAYSTACK WEBHOOK
# ============================================================

@csrf_exempt
@require_POST
def paystack_webhook(request):
    """
    Paystack server-to-server webhook.

    Paystack signs webhook requests using the secret key.
    The signature is verified before processing the event.
    """

    secret_key = (
        settings.PAYSTACK_SECRET_KEY
    )

    if not secret_key:

        logger.error(
            "PAYSTACK_SECRET_KEY is missing for webhook."
        )

        return HttpResponse(
            status=500
        )

    signature = request.headers.get(
        "X-Paystack-Signature",
        "",
    )

    if not signature:

        logger.warning(
            "Paystack webhook received without signature."
        )

        return HttpResponse(
            status=401
        )

    expected_signature = hmac.new(
        secret_key.encode("utf-8"),
        request.body,
        hashlib.sha512,
    ).hexdigest()

    if not hmac.compare_digest(
        signature,
        expected_signature,
    ):

        logger.warning(
            "Invalid Paystack webhook signature."
        )

        return HttpResponse(
            status=401
        )

    # --------------------------------------------------------
    # PARSE PAYLOAD
    # --------------------------------------------------------

    try:

        payload = json.loads(
            request.body.decode("utf-8")
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):

        logger.warning(
            "Invalid JSON received from Paystack webhook."
        )

        return HttpResponse(
            status=400
        )

    event = payload.get(
        "event"
    )

    data = (
        payload.get("data")
        or {}
    )

    # --------------------------------------------------------
    # WE ONLY NEED SUCCESSFUL CHARGE EVENTS
    # --------------------------------------------------------

    if event != "charge.success":

        return HttpResponse(
            status=200
        )

    reference = (
        data.get("reference")
        or ""
    ).strip()

    if not reference:

        logger.warning(
            "Paystack webhook charge.success without reference."
        )

        return HttpResponse(
            status=400
        )

    # --------------------------------------------------------
    # FIND SUBSCRIPTION
    # --------------------------------------------------------

    subscription_record = (
        Subscription.objects
        .filter(
            payment_reference=reference,
        )
        .first()
    )

    if not subscription_record:

        logger.warning(
            "Webhook received for unknown reference: %s",
            reference,
        )

        return HttpResponse(
            status=200
        )

    # --------------------------------------------------------
    # ALREADY ACTIVE
    # --------------------------------------------------------

    if subscription_record.status == "ACTIVE":

        return HttpResponse(
            status=200
        )

    # --------------------------------------------------------
    # ONLY PENDING PAYMENTS
    # --------------------------------------------------------

    if subscription_record.status != "PENDING":

        logger.warning(
            "Webhook ignored for non-pending subscription. "
            "Reference=%s Status=%s",
            reference,
            subscription_record.status,
        )

        return HttpResponse(
            status=200
        )

    # --------------------------------------------------------
    # VERIFY REFERENCE
    # --------------------------------------------------------

    returned_reference = (
        data.get("reference")
        or ""
    ).strip()

    if not hmac.compare_digest(
        returned_reference,
        subscription_record.payment_reference,
    ):

        logger.error(
            "Webhook reference mismatch."
        )

        return HttpResponse(
            status=400
        )

    # --------------------------------------------------------
    # VERIFY STATUS
    # --------------------------------------------------------

    if data.get("status") != "success":

        return HttpResponse(
            status=200
        )

    # --------------------------------------------------------
    # VERIFY CURRENCY
    # --------------------------------------------------------

    currency = (
        data.get("currency")
        or ""
    ).upper()

    if currency != "GHS":

        logger.error(
            "Webhook currency mismatch. Currency=%s",
            currency,
        )

        subscription_record.payment_status = (
            "CURRENCY_MISMATCH"
        )

        subscription_record.save(
            update_fields=[
                "payment_status",
                "updated_at",
            ]
        )

        return HttpResponse(
            status=400
        )

    # --------------------------------------------------------
    # VERIFY AMOUNT
    # --------------------------------------------------------

    expected_amount = (
        subscription_amount_in_subunit(
            subscription_record.amount
        )
    )

    if data.get("amount") != expected_amount:

        logger.error(
            "Webhook amount mismatch. "
            "Expected=%s Received=%s Reference=%s",
            expected_amount,
            data.get("amount"),
            reference,
        )

        subscription_record.payment_status = (
            "AMOUNT_MISMATCH"
        )

        subscription_record.save(
            update_fields=[
                "payment_status",
                "updated_at",
            ]
        )

        return HttpResponse(
            status=400
        )

    # --------------------------------------------------------
    # PLAN
    # --------------------------------------------------------

    plan_data = SUBSCRIPTION_PLANS.get(
        subscription_record.plan
    )

    if not plan_data:

        logger.error(
            "Webhook contains invalid subscription plan. "
            "Subscription=%s",
            subscription_record.id,
        )

        return HttpResponse(
            status=400
        )

    # --------------------------------------------------------
    # ACTIVATE
    # --------------------------------------------------------

    with transaction.atomic():

        locked_subscription = (
            Subscription.objects
            .select_for_update()
            .select_related("store")
            .get(
                pk=subscription_record.pk
            )
        )

        # Another request may have activated it.
        if locked_subscription.status == "ACTIVE":

            return HttpResponse(
                status=200
            )

        if locked_subscription.status != "PENDING":

            return HttpResponse(
                status=200
            )

        now = timezone.now()

        existing_active = (
            locked_subscription.store.active_subscription
        )

        if existing_active:

            start_date = (
                existing_active.end_date
            )

        else:

            start_date = now

        end_date = (
            start_date
            + timedelta(
                days=plan_data["days"]
            )
        )

        locked_subscription.start_date = start_date
        locked_subscription.end_date = end_date
        locked_subscription.status = "ACTIVE"
        locked_subscription.payment_status = "PAID"

        locked_subscription.save(
            update_fields=[
                "start_date",
                "end_date",
                "status",
                "payment_status",
                "updated_at",
            ]
        )

    logger.info(
        "Paystack webhook activated subscription. "
        "Subscription=%s Reference=%s",
        subscription_record.id,
        reference,
    )

    return HttpResponse(
        status=200
    )


# ============================================================
# DEVELOPMENT-ONLY TEST SUBSCRIPTION
# ============================================================

@login_required
@require_POST
def test_activate_subscription(request):
    """
    Development-only subscription activation.

    This must never activate a real subscription when
    DEBUG=False.
    """

    if not settings.DEBUG:

        return redirect(
            "subscription"
        )

    store = get_current_store(request)

    if not store:

        messages.error(
            request,
            "You are not assigned to an active store.",
        )

        return redirect(
            "dashboard"
        )

    if not (
        is_main_admin(request.user)
        or request.user.role in ["ADMIN", "MANAGER"]
    ):

        messages.error(
            request,
            (
                "You do not have permission to "
                "activate a subscription."
            ),
        )

        return redirect(
            "subscription"
        )

    plan = (
        request.POST.get(
            "plan",
            "",
        )
        .strip()
        .upper()
    )

    selected_plan = SUBSCRIPTION_PLANS.get(
        plan
    )

    if not selected_plan:

        messages.error(
            request,
            "Invalid subscription plan.",
        )

        return redirect(
            "subscription"
        )

    if store.subscription_active:

        messages.warning(
            request,
            "Your store already has an active subscription.",
        )

        return redirect(
            "subscription"
        )

    now = timezone.now()

    Subscription.objects.create(
        store=store,
        plan=plan,
        amount=selected_plan["amount"],
        start_date=now,
        end_date=(
            now
            + timedelta(
                days=selected_plan["days"]
            )
        ),
        status="ACTIVE",
        payment_reference=(
            f"TEST-{uuid.uuid4().hex[:12].upper()}"
        ),
        payment_status="PAID",
    )

    messages.success(
        request,
        (
            f"TEST MODE: {selected_plan['name']} "
            f"subscription activated for "
            f"{selected_plan['days']} days."
        ),
    )

    return redirect(
        "subscription"
    )


# ============================================================
# USER MANAGEMENT
# ============================================================

@login_required
def manage_users(request):

    if not can_manage_users(request.user):

        messages.error(
            request,
            (
                "You do not have permission to "
                "access user management."
            ),
        )

        return redirect(
            "dashboard"
        )

    users = (
        User.objects
        .select_related("store")
        .exclude(
            id=request.user.id
        )
        .order_by(
            "store__name",
            "username",
        )
    )

    return render(
        request,
        "core/manage_users.html",
        {
            "users": users,
        },
    )


@login_required
def add_user(request):

    if not can_manage_users(request.user):

        messages.error(
            request,
            "You do not have permission to add users.",
        )

        return redirect(
            "dashboard"
        )

    stores = (
        Store.objects
        .filter(active=True)
        .order_by("name")
    )

    if request.method == "POST":

        username = request.POST.get(
            "username",
            "",
        ).strip()

        first_name = request.POST.get(
            "first_name",
            "",
        ).strip()

        last_name = request.POST.get(
            "last_name",
            "",
        ).strip()

        email = request.POST.get(
            "email",
            "",
        ).strip()

        role = (
            request.POST.get(
                "role",
                "",
            )
            .strip()
            .upper()
        )

        store_id = request.POST.get(
            "store",
            "",
        ).strip()

        password = request.POST.get(
            "password",
            "",
        )

        password_confirm = request.POST.get(
            "password_confirm",
            "",
        )

        # ----------------------------------------------------
        # VALIDATION
        # ----------------------------------------------------

        if not username:

            messages.error(
                request,
                "Username is required.",
            )

            return redirect(
                "add_user"
            )

        if not role:

            messages.error(
                request,
                "Please select a user role.",
            )

            return redirect(
                "add_user"
            )

        if role not in [
            "ADMIN",
            "MANAGER",
            "ATTENDANT",
        ]:

            messages.error(
                request,
                "Invalid user role.",
            )

            return redirect(
                "add_user"
            )

        if not store_id:

            messages.error(
                request,
                "Please select a store.",
            )

            return redirect(
                "add_user"
            )

        if not password:

            messages.error(
                request,
                "Password is required.",
            )

            return redirect(
                "add_user"
            )

        if password != password_confirm:

            messages.error(
                request,
                "Passwords do not match.",
            )

            return redirect(
                "add_user"
            )

        # ----------------------------------------------------
        # USERNAME
        # ----------------------------------------------------

        if User.objects.filter(
            username__iexact=username
        ).exists():

            messages.error(
                request,
                (
                    f"The username '{username}' "
                    "already exists."
                ),
            )

            return redirect(
                "add_user"
            )

        # ----------------------------------------------------
        # STORE
        # ----------------------------------------------------

        store = get_object_or_404(
            Store,
            id=store_id,
            active=True,
        )

        # ----------------------------------------------------
        # PASSWORD VALIDATION
        # ----------------------------------------------------

        try:

            validate_password(
                password
            )

        except ValidationError as error:

            for message in error.messages:

                messages.error(
                    request,
                    message,
                )

            return redirect(
                "add_user"
            )

        # ----------------------------------------------------
        # CREATE USER
        # ----------------------------------------------------

        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )

        user.role = role
        user.store = store
        user.is_active = True
        user.is_staff = False
        user.is_superuser = False

        user.save(
            update_fields=[
                "role",
                "store",
                "is_active",
                "is_staff",
                "is_superuser",
            ]
        )

        messages.success(
            request,
            (
                f"User '{username}' was created successfully "
                f"as {user.get_role_display()}."
            ),
        )

        return redirect(
            "manage_users"
        )

    return render(
        request,
        "core/add_user.html",
        {
            "stores": stores,
        },
    )


@login_required
def edit_user(request, user_id):

    if not can_manage_users(request.user):

        messages.error(
            request,
            "You do not have permission to edit users.",
        )

        return redirect(
            "dashboard"
        )

    user = get_object_or_404(
        User.objects.select_related("store"),
        id=user_id,
    )

    if user.id == request.user.id:

        messages.error(
            request,
            (
                "You cannot edit your own Super Admin "
                "account here."
            ),
        )

        return redirect(
            "manage_users"
        )

    stores = (
        Store.objects
        .filter(active=True)
        .order_by("name")
    )

    if request.method == "POST":

        username = request.POST.get(
            "username",
            "",
        ).strip()

        email = request.POST.get(
            "email",
            "",
        ).strip()

        role = (
            request.POST.get(
                "role",
                "ATTENDANT",
            )
            .strip()
            .upper()
        )

        store_id = request.POST.get(
            "store",
            "",
        ).strip()

        if not username:

            messages.error(
                request,
                "Username is required.",
            )

            return redirect(
                "edit_user",
                user_id=user.id,
            )

        if role not in [
            "ADMIN",
            "MANAGER",
            "ATTENDANT",
        ]:

            messages.error(
                request,
                "Invalid user role.",
            )

            return redirect(
                "edit_user",
                user_id=user.id,
            )

        if User.objects.filter(
            username__iexact=username
        ).exclude(
            id=user.id
        ).exists():

            messages.error(
                request,
                (
                    f"The username '{username}' "
                    "is already in use."
                ),
            )

            return redirect(
                "edit_user",
                user_id=user.id,
            )

        if not store_id:

            messages.error(
                request,
                "Please select a store.",
            )

            return redirect(
                "edit_user",
                user_id=user.id,
            )

        store = get_object_or_404(
            Store,
            id=store_id,
            active=True,
        )

        user.username = username
        user.email = email
        user.role = role
        user.store = store

        user.save()

        messages.success(
            request,
            (
                f"User '{user.username}' "
                "was updated successfully."
            ),
        )

        return redirect(
            "manage_users"
        )

    return render(
        request,
        "core/edit_user.html",
        {
            "user_account": user,
            "stores": stores,
        },
    )


@login_required
def profile(request):

    user = request.user

    if request.method == "POST":

        action = request.POST.get(
            "action",
            "profile",
        )

        # ----------------------------------------------------
        # PROFILE
        # ----------------------------------------------------

        if action == "profile":

            first_name = request.POST.get(
                "first_name",
                "",
            ).strip()

            last_name = request.POST.get(
                "last_name",
                "",
            ).strip()

            username = request.POST.get(
                "username",
                "",
            ).strip()

            email = request.POST.get(
                "email",
                "",
            ).strip()

            if not username:

                messages.error(
                    request,
                    "Username cannot be empty.",
                )

                return redirect(
                    "profile"
                )

            if (
                User.objects
                .filter(
                    username__iexact=username
                )
                .exclude(
                    pk=user.pk
                )
                .exists()
            ):

                messages.error(
                    request,
                    "That username is already in use.",
                )

                return redirect(
                    "profile"
                )

            user.first_name = first_name
            user.last_name = last_name
            user.username = username
            user.email = email

            user.save(
                update_fields=[
                    "first_name",
                    "last_name",
                    "username",
                    "email",
                ]
            )

            messages.success(
                request,
                "Your profile has been updated successfully.",
            )

            return redirect(
                "profile"
            )

        # ----------------------------------------------------
        # PASSWORD
        # ----------------------------------------------------

        if action == "password":

            password_form = PasswordChangeForm(
                user,
                request.POST,
            )

            if password_form.is_valid():

                changed_user = (
                    password_form.save()
                )

                update_session_auth_hash(
                    request,
                    changed_user,
                )

                messages.success(
                    request,
                    "Your password has been changed successfully.",
                )

                return redirect(
                    "profile"
                )

            for field_errors in (
                password_form.errors.values()
            ):

                for error in field_errors:

                    messages.error(
                        request,
                        error,
                    )

            return redirect(
                "profile"
            )

    password_form = PasswordChangeForm(
        user
    )

    store = getattr(
        user,
        "store",
        None,
    )

    if user.is_superuser:

        role_display = "Super Admin"

    else:

        role_display = user.get_role_display()

    return render(
        request,
        "core/profile.html",
        {
            "password_form": password_form,
            "profile_user": user,
            "profile_store": store,
            "role_display": role_display,
        },
    )


@login_required
@require_POST
def toggle_user(request, user_id):

    if not can_manage_users(request.user):

        messages.error(
            request,
            (
                "You do not have permission "
                "to perform this action."
            ),
        )

        return redirect(
            "dashboard"
        )

    user = get_object_or_404(
        User,
        id=user_id,
    )

    if user.id == request.user.id:

        messages.error(
            request,
            (
                "You cannot deactivate your "
                "own Super Admin account."
            ),
        )

        return redirect(
            "manage_users"
        )

    user.is_active = not user.is_active

    user.save(
        update_fields=[
            "is_active"
        ]
    )

    status = (
        "activated"
        if user.is_active
        else "deactivated"
    )

    messages.success(
        request,
        (
            f"User '{user.username}' "
            f"was {status}."
        ),
    )

    return redirect(
        "manage_users"
    )


@login_required
@require_POST
def delete_user(request, user_id):

    if not can_manage_users(request.user):

        messages.error(
            request,
            (
                "You do not have permission "
                "to delete users."
            ),
        )

        return redirect(
            "dashboard"
        )

    user = get_object_or_404(
        User,
        id=user_id,
    )

    if user.id == request.user.id:

        messages.error(
            request,
            (
                "You cannot delete your own "
                "Super Admin account."
            ),
        )

        return redirect(
            "manage_users"
        )

    if Sale.objects.filter(
        sold_by=user
    ).exists():

        messages.error(
            request,
            (
                f"'{user.username}' has sales history "
                "and cannot be permanently deleted. "
                "Deactivate the account instead."
            ),
        )

        return redirect(
            "manage_users"
        )

    username = user.username

    user.delete()

    messages.success(
        request,
        (
            f"User '{username}' "
            "was deleted successfully."
        ),
    )

    return redirect(
        "manage_users"
    )


@login_required
def reset_user_password(request, user_id):

    if not can_manage_users(request.user):

        messages.error(
            request,
            "You do not have permission to reset passwords.",
        )

        return redirect(
            "dashboard"
        )

    user = get_object_or_404(
        User,
        id=user_id,
    )

    if user.id == request.user.id:

        messages.error(
            request,
            (
                "Use your account settings "
                "to change your own password."
            ),
        )

        return redirect(
            "manage_users"
        )

    if request.method == "POST":

        password = request.POST.get(
            "password",
            "",
        ).strip()

        password_confirm = request.POST.get(
            "password_confirm",
            "",
        ).strip()

        if not password:

            messages.error(
                request,
                "Password is required.",
            )

            return redirect(
                "reset_user_password",
                user_id=user.id,
            )

        if password != password_confirm:

            messages.error(
                request,
                "Passwords do not match.",
            )

            return redirect(
                "reset_user_password",
                user_id=user.id,
            )

        try:

            validate_password(
                password,
                user,
            )

        except ValidationError as error:

            for message in error.messages:

                messages.error(
                    request,
                    message,
                )

            return redirect(
                "reset_user_password",
                user_id=user.id,
            )

        user.set_password(
            password
        )

        user.save(
            update_fields=[
                "password"
            ]
        )

        messages.success(
            request,
            (
                f"Password for '{user.username}' "
                "was reset successfully."
            ),
        )

        return redirect(
            "manage_users"
        )

    return render(
        request,
        "core/reset_user_password.html",
        {
            "user_account": user,
        },
    )


# ============================================================
# STORE MANAGEMENT
# ============================================================

@login_required
def manage_stores(request):

    if not can_manage_stores(request.user):

        messages.error(
            request,
            "You do not have permission to manage stores.",
        )

        return redirect(
            "dashboard"
        )

    stores = (
        Store.objects
        .all()
        .order_by("-created_at")
    )

    store_data = []

    for store in stores:

        store_data.append({
            "store": store,
            "users_count": (
                User.objects
                .filter(store=store)
                .count()
            ),
        })

    return render(
        request,
        "core/manage_stores.html",
        {
            "store_data": store_data,
        },
    )


@login_required
def add_store(request):

    if not can_manage_stores(request.user):

        messages.error(
            request,
            "You do not have permission to add stores.",
        )

        return redirect(
            "dashboard"
        )

    if request.method == "POST":

        name = request.POST.get(
            "name",
            "",
        ).strip()

        phone = request.POST.get(
            "phone",
            "",
        ).strip()

        address = request.POST.get(
            "address",
            "",
        ).strip()

        if not name:

            messages.error(
                request,
                "Store name is required.",
            )

            return redirect(
                "add_store"
            )

        Store.objects.create(
            name=name,
            phone=phone,
            address=address,
            active=True,
        )

        messages.success(
            request,
            (
                f"Store '{name}' "
                "was created successfully."
            ),
        )

        return redirect(
            "manage_stores"
        )

    return render(
        request,
        "core/add_store.html",
    )


@login_required
def edit_store(request, store_id):

    if not can_manage_stores(request.user):

        messages.error(
            request,
            "You do not have permission to edit stores.",
        )

        return redirect(
            "dashboard"
        )

    store = get_object_or_404(
        Store,
        id=store_id,
    )

    if request.method == "POST":

        name = request.POST.get(
            "name",
            "",
        ).strip()

        phone = request.POST.get(
            "phone",
            "",
        ).strip()

        address = request.POST.get(
            "address",
            "",
        ).strip()

        if not name:

            messages.error(
                request,
                "Store name is required.",
            )

            return redirect(
                "edit_store",
                store_id=store.id,
            )

        store.name = name
        store.phone = phone
        store.address = address

        store.save()

        messages.success(
            request,
            "Store details updated successfully.",
        )

        return redirect(
            "manage_stores"
        )

    return render(
        request,
        "core/edit_store.html",
        {
            "store": store,
        },
    )


@login_required
@require_POST
def toggle_store(request, store_id):

    if not can_manage_stores(request.user):

        messages.error(
            request,
            (
                "You do not have permission "
                "to perform this action."
            ),
        )

        return redirect(
            "dashboard"
        )

    store = get_object_or_404(
        Store,
        id=store_id,
    )

    store.active = not store.active

    store.save(
        update_fields=[
            "active"
        ]
    )

    status = (
        "activated"
        if store.active
        else "deactivated"
    )

    messages.success(
        request,
        (
            f"Store '{store.name}' "
            f"was {status}."
        ),
    )

    return redirect(
        "manage_stores"
    )


@login_required
@require_POST
def delete_store(request, store_id):

    if not can_manage_stores(request.user):

        messages.error(
            request,
            "You do not have permission to delete stores.",
        )

        return redirect(
            "dashboard"
        )

    store = get_object_or_404(
        Store,
        id=store_id,
    )

    if User.objects.filter(
        store=store
    ).exists():

        messages.error(
            request,
            (
                "This store cannot be deleted because "
                "it has users. Deactivate the store instead."
            ),
        )

        return redirect(
            "manage_stores"
        )

    if Product.objects.filter(
        store=store
    ).exists():

        messages.error(
            request,
            (
                "This store cannot be deleted because "
                "it has products. Deactivate the store instead."
            ),
        )

        return redirect(
            "manage_stores"
        )

    if Sale.objects.filter(
        store=store
    ).exists():

        messages.error(
            request,
            (
                "This store cannot be deleted because "
                "it has sales history. Deactivate the store instead."
            ),
        )

        return redirect(
            "manage_stores"
        )

    if StockMovement.objects.filter(
        store=store
    ).exists():

        messages.error(
            request,
            (
                "This store cannot be deleted because "
                "it has stock history. Deactivate the store instead."
            ),
        )

        return redirect(
            "manage_stores"
        )

    store_name = store.name

    store.delete()

    messages.success(
        request,
        (
            f"Store '{store_name}' "
            "was deleted successfully."
        ),
    )

    return redirect(
        "manage_stores"
    )


# ============================================================
# SUPER ADMIN - SUBSCRIPTION MANAGEMENT
# ============================================================

@login_required
def manage_subscriptions(request):
    if not request.user.is_superuser:
        messages.error(
            request,
            "You do not have permission to manage subscriptions."
        )
        return redirect("dashboard")

    stores = Store.objects.all().order_by("name")

    store_data = []

    active_subscription_count = 0
    trial_store_count = 0
    expired_store_count = 0

    for store in stores:

        active_subscription = store.active_subscription

        latest_subscription = (
            Subscription.objects
            .filter(store=store)
            .order_by("-created_at")
            .first()
        )

        status = subscription_status(store)

        if status["status"] == "ACTIVE":
            active_subscription_count += 1

        elif status["status"] == "TRIAL":
            trial_store_count += 1

        elif status["status"] == "EXPIRED":
            expired_store_count += 1

        store_data.append({
            "store": store,
            "active_subscription": active_subscription,
            "latest_subscription": latest_subscription,
            "status": status,
        })

    return render(
        request,
        "core/manage_subscriptions.html",
        {
            "store_data": store_data,
            "active_subscription_count": active_subscription_count,
            "trial_store_count": trial_store_count,
            "expired_store_count": expired_store_count,
        },
    )


# ============================================================
# SUPER ADMIN - MANUALLY ACTIVATE SUBSCRIPTION
# ============================================================

@login_required
@require_POST
def admin_activate_subscription(
    request,
    store_id,
):

    if not is_main_admin(request.user):

        messages.error(
            request,
            "You do not have permission to activate subscriptions.",
        )

        return redirect("dashboard")

    store = get_object_or_404(
        Store,
        id=store_id,
    )

    plan = (
        request.POST.get(
            "plan",
            "",
        )
        .strip()
        .upper()
    )

    selected_plan = SUBSCRIPTION_PLANS.get(
        plan
    )

    if not selected_plan:

        messages.error(
            request,
            "Invalid subscription plan.",
        )

        return redirect(
            "manage_subscriptions"
        )

    # --------------------------------------------------------
    # PREVENT DUPLICATE ACTIVE SUBSCRIPTIONS
    # --------------------------------------------------------

    if store.subscription_active:

        messages.warning(
            request,
            (
                f"{store.name} already has an active "
                "subscription. Use Extend instead."
            ),
        )

        return redirect(
            "manage_subscriptions"
        )

    now = timezone.now()

    # --------------------------------------------------------
    # CREATE MANUAL SUBSCRIPTION
    # --------------------------------------------------------

    with transaction.atomic():

        Subscription.objects.filter(
            store=store,
            status="PENDING",
        ).update(
            status="CANCELLED",
            payment_status="CANCELLED",
        )

        Subscription.objects.create(
            store=store,
            plan=plan,
            amount=selected_plan["amount"],
            start_date=now,
            end_date=(
                now
                + timedelta(
                    days=selected_plan["days"]
                )
            ),
            status="ACTIVE",
            payment_reference=(
                f"ADMIN-{uuid.uuid4().hex[:16].upper()}"
            ),
            payment_status="ADMIN_ACTIVATED",
        )

    messages.success(
        request,
        (
            f"{selected_plan['name']} subscription "
            f"was manually activated for "
            f"'{store.name}' for "
            f"{selected_plan['days']} days."
        ),
    )

    return redirect(
        "manage_subscriptions"
    )


# ============================================================
# SUPER ADMIN - EXTEND SUBSCRIPTION
# ============================================================

@login_required
@require_POST
def admin_extend_subscription(
    request,
    subscription_id,
):

    if not is_main_admin(request.user):

        messages.error(
            request,
            "You do not have permission to extend subscriptions.",
        )

        return redirect("dashboard")

    plan = (
        request.POST.get(
            "plan",
            "",
        )
        .strip()
        .upper()
    )

    selected_plan = SUBSCRIPTION_PLANS.get(
        plan
    )

    if not selected_plan:

        messages.error(
            request,
            "Invalid subscription plan.",
        )

        return redirect(
            "manage_subscriptions"
        )

    # --------------------------------------------------------
    # LOCK SUBSCRIPTION
    # --------------------------------------------------------

    with transaction.atomic():

        subscription = (
            Subscription.objects
            .select_for_update()
            .select_related("store")
            .filter(
                id=subscription_id
            )
            .first()
        )

        if not subscription:

            messages.error(
                request,
                "Subscription could not be found.",
            )

            return redirect(
                "manage_subscriptions"
            )

        store = subscription.store

        now = timezone.now()

        # ----------------------------------------------------
        # FIND CURRENT ACTIVE SUBSCRIPTION
        # ----------------------------------------------------

        active_subscription = (
            store.active_subscription
        )

        if active_subscription:

            # Extend from the current expiry date.
            start_date = (
                active_subscription.end_date
            )

        else:

            # If already expired, start from now.
            start_date = now

        end_date = (
            start_date
            + timedelta(
                days=selected_plan["days"]
            )
        )

        # ----------------------------------------------------
        # EXPIRE OTHER ACTIVE SUBSCRIPTIONS
        # ----------------------------------------------------

        Subscription.objects.filter(
            store=store,
            status="ACTIVE",
        ).exclude(
            id=subscription.id,
        ).update(
            status="EXPIRED",
        )

        # ----------------------------------------------------
        # UPDATE THIS SUBSCRIPTION
        # ----------------------------------------------------

        subscription.plan = plan
        subscription.amount = (
            selected_plan["amount"]
        )
        subscription.start_date = start_date
        subscription.end_date = end_date
        subscription.status = "ACTIVE"
        subscription.payment_status = "ADMIN_EXTENDED"

        subscription.payment_reference = (
            subscription.payment_reference
            or f"ADMIN-{uuid.uuid4().hex[:16].upper()}"
        )

        subscription.save(
            update_fields=[
                "plan",
                "amount",
                "start_date",
                "end_date",
                "status",
                "payment_status",
                "payment_reference",
                "updated_at",
            ]
        )

    messages.success(
        request,
        (
            f"{selected_plan['name']} subscription "
            f"for '{store.name}' was extended by "
            f"{selected_plan['days']} days."
        ),
    )

    return redirect(
        "manage_subscriptions"
    )


# ============================================================
# SUPER ADMIN - CANCEL SUBSCRIPTION
# ============================================================

@login_required
@require_POST
def admin_cancel_subscription(
    request,
    subscription_id,
):

    if not is_main_admin(request.user):

        messages.error(
            request,
            "You do not have permission to cancel subscriptions.",
        )

        return redirect("dashboard")

    subscription = get_object_or_404(
        Subscription.objects.select_related("store"),
        id=subscription_id,
    )

    subscription.status = "CANCELLED"
    subscription.payment_status = (
        "CANCELLED_BY_ADMIN"
    )

    subscription.save(
        update_fields=[
            "status",
            "payment_status",
            "updated_at",
        ]
    )

    messages.success(
        request,
        (
            f"Subscription for '{subscription.store.name}' "
            "was cancelled."
        ),
    )

    return redirect(
        "manage_subscriptions"
    )


# ============================================================
# SUPER ADMIN - FORCE EXPIRE SUBSCRIPTION
# ============================================================

@login_required
@require_POST
def admin_expire_subscription(
    request,
    subscription_id,
):

    if not is_main_admin(request.user):

        messages.error(
            request,
            "You do not have permission to expire subscriptions.",
        )

        return redirect("dashboard")

    subscription = get_object_or_404(
        Subscription.objects.select_related("store"),
        id=subscription_id,
    )

    subscription.status = "EXPIRED"
    subscription.payment_status = (
        "EXPIRED_BY_ADMIN"
    )

    subscription.save(
        update_fields=[
            "status",
            "payment_status",
            "updated_at",
        ]
    )

    messages.success(
        request,
        (
            f"Subscription for '{subscription.store.name}' "
            "was marked as expired."
        ),
    )

    return redirect(
        "manage_subscriptions"
    )


# ============================================================
# DASHBOARD
# ============================================================

@login_required
def dashboard(request):

    store = get_current_store(request)

    if not store:

        if is_main_admin(request.user):
            return redirect(
                "manage_stores"
            )

        return render(
            request,
            "core/no_store.html",
        )

    active_products_count = (
        Product.objects
        .filter(
            store=store,
            active=True,
        )
        .count()
    )

    total_sales = sum(
        (
            sale.total_price
            for sale in (
                Sale.objects
                .filter(store=store)
            )
        ),
        Decimal("0.00"),
    )

    today = timezone.localdate()

    days_since_sunday = (
        today.weekday() + 1
    ) % 7

    week_start = (
        today
        - datetime.timedelta(
            days=days_since_sunday
        )
    )

    week_end = (
        week_start
        + datetime.timedelta(
            days=6
        )
    )

    weekly_sales = (
        Sale.objects
        .filter(
            store=store,
            created_at__date__gte=week_start,
            created_at__date__lte=week_end,
        )
    )

    weekly_sales_data = []

    for i in range(7):

        current_day = (
            week_start
            + datetime.timedelta(
                days=i
            )
        )

        day_sales = sum(
            (
                sale.total_price
                for sale in weekly_sales
                if timezone.localtime(
                    sale.created_at
                ).date() == current_day
            ),
            Decimal("0.00"),
        )

        weekly_sales_data.append({
            "day": current_day.strftime(
                "%A"
            ),
            "date": current_day.strftime(
                "%Y-%m-%d"
            ),
            "amount": day_sales,
        })

    return render(
        request,
        "core/dashboard.html",
        {
            "store": store,
            "attendants": (
                User.objects.filter(
                    store=store, role="ATTENDANT",
                    is_superuser=False, is_staff=False,
                ).order_by("username")
                if request.user.is_superuser or request.user.role in ["ADMIN", "MANAGER"]
                else User.objects.none()
            ),
            "active_products_count": (
                active_products_count
            ),
            "total_sales": total_sales,
            "weekly_sales_data": (
                weekly_sales_data
            ),
            "week_start": week_start,
            "week_end": week_end,
            "subscription": (
                subscription_status(store)
            ),
        },
    )


# ============================================================
# ACTIVE PRODUCTS
# ============================================================

@login_required
def active_products(request):

    store = get_current_store(request)

    if not store:

        if is_main_admin(request.user):
            return redirect(
                "manage_stores"
            )

        return render(
            request,
            "core/no_store.html",
        )

    products = (
        Product.objects
        .filter(
            store=store,
            active=True,
        )
        .select_related("category")
        .order_by("name")
    )

    total_products = products.count()

    low_stock_count = sum(
        1
        for product in products
        if product.is_low_stock
    )

    return render(
        request,
        "core/active_products.html",
        {
            "products": products,
            "store": store,
            "total_products": total_products,
            "low_stock_count": low_stock_count,
        },
    )


@login_required
@require_POST
def delete_product(request, product_id):

    if not can_manage_products(request.user):

        messages.error(
            request,
            "You do not have permission to delete products.",
        )

        return redirect(
            "active_products"
        )

    store = get_current_store(request)

    if not store:

        messages.error(
            request,
            "You are not assigned to an active store.",
        )

        return redirect(
            "dashboard"
        )

    if not has_product_management_access(store):

        messages.warning(
            request,
            (
                "Your 30-day free trial has expired. "
                "Please subscribe to continue managing products."
            ),
        )

        return redirect("subscription")

    product = get_object_or_404(
        Product,
        id=product_id,
        store=store,
    )

    if Sale.objects.filter(
        product=product
    ).exists():

        messages.error(
            request,
            (
                f'"{product.name}" has sales history '
                "and cannot be permanently deleted. "
                "Deactivate it instead."
            ),
        )

        return redirect(
            "active_products"
        )

    product_name = product.name

    product.delete()

    messages.success(
        request,
        (
            f'"{product_name}" '
            "was deleted successfully."
        ),
    )

    return redirect(
        "active_products"
    )


@login_required
@require_POST
def reset_active_products(request):

    if not can_reset_data(request.user):

        messages.error(
            request,
            "You do not have permission to reset products.",
        )

        return redirect(
            "active_products"
        )

    store = get_current_store(request)

    if not store:

        messages.error(
            request,
            "You are not assigned to an active store.",
        )

        return redirect(
            "dashboard"
        )

    if not has_product_management_access(store):

        messages.warning(
            request,
            (
                "Your 30-day free trial has expired. "
                "Please subscribe to continue managing products."
            ),
        )

        return redirect("subscription")

    password = request.POST.get(
        "password",
        "",
    )

    if not password:

        messages.error(
            request,
            "Please enter your password.",
        )

        return redirect(
            "active_products"
        )

    if not request.user.check_password(
        password
    ):

        messages.error(
            request,
            "Incorrect password. Products were not reset.",
        )

        return redirect(
            "active_products"
        )

    with transaction.atomic():

        Product.objects.filter(
            store=store,
            active=True,
        ).update(
            active=False
        )

    messages.success(
        request,
        "All active products have been reset.",
    )

    return redirect(
        "active_products"
    )


# ============================================================
# DAILY SALES
# ============================================================

@login_required
def daily_sales(request):

    store = get_current_store(request)

    if not store:

        if is_main_admin(request.user):
            return redirect(
                "manage_stores"
            )

        return render(
            request,
            "core/no_store.html",
        )

    date_value = request.GET.get(
        "date",
        "",
    )

    try:

        selected_date = (
            datetime.datetime.strptime(
                date_value,
                "%Y-%m-%d",
            ).date()
            if date_value
            else timezone.localdate()
        )

    except ValueError:

        selected_date = timezone.localdate()

    sales = (
        Sale.objects
        .filter(
            store=store,
            created_at__date=selected_date,
        )
        .select_related(
            "product",
            "sold_by",
        )
        .order_by("-created_at")
    )

    daily_total = sum(
        (
            sale.total_price
            for sale in sales
        ),
        Decimal("0.00"),
    )

    return render(
        request,
        "core/daily_sales.html",
        {
            "sales": sales,
            "selected_date": selected_date,
            "daily_total": daily_total,
            "store": store,
        },
    )


# ============================================================
# ALL SALES
# ============================================================

@login_required
def all_sales(request):

    store = get_current_store(request)

    if not store:

        if is_main_admin(request.user):
            return redirect(
                "manage_stores"
            )

        return render(
            request,
            "core/no_store.html",
        )

    if request.method == "POST":

        if not can_reset_data(request.user):

            messages.error(
                request,
                "You do not have permission to reset sales.",
            )

            return redirect(
                "all_sales"
            )

        action = (
            request.POST.get(
                "action",
                "",
            )
            .strip()
        )

        if action == "reset_sales":

            password = request.POST.get(
                "password",
                "",
            )

            if not password:

                messages.error(
                    request,
                    (
                        "Please enter your password "
                        "to reset sales."
                    ),
                )

                return redirect(
                    "all_sales"
                )

            user = authenticate(
                request=request,
                username=request.user.username,
                password=password,
            )

            if user is None:

                messages.error(
                    request,
                    "Incorrect password. Sales were not reset.",
                )

                return redirect(
                    "all_sales"
                )

            try:

                with transaction.atomic():

                    deleted_count, _ = (
                        Sale.objects
                        .filter(
                            store=store
                        )
                        .delete()
                    )

                messages.success(
                    request,
                    (
                        "Sales history reset successfully. "
                        f"{deleted_count} sales record(s) "
                        "were removed."
                    ),
                )

            except Exception:

                logger.exception(
                    "Sales reset failed."
                )

                messages.error(
                    request,
                    (
                        "Sales could not be reset. "
                        "Please try again."
                    ),
                )

            return redirect(
                "all_sales"
            )

    sales = (
        Sale.objects
        .filter(store=store)
        .select_related(
            "product",
            "sold_by",
        )
        .order_by("-created_at")
    )

    total_sales = sum(
        (
            sale.total_price
            for sale in sales
        ),
        Decimal("0.00"),
    )

    total_items = sum(
        sale.quantity
        for sale in sales
    )

    sales_count = sales.count()

    profit_context = {"can_view_profit": can_manage_products(request.user)}
    if profit_context["can_view_profit"]:
        total_cost = sum((sale.cost_total for sale in sales), Decimal("0.00"))
        gross_profit = total_sales - total_cost
        profit_margin = (
            gross_profit / total_sales * Decimal("100")
            if total_sales > 0 else None
        )
        profit_context.update({
            "total_cost": total_cost,
            "gross_profit": gross_profit,
            "profit_margin_display": (
                f"{profit_margin:.2f}%" if profit_margin is not None else "—"
            ),
            "estimated_cost_sales_count": sum(sale.cost_is_estimated for sale in sales),
        })

    return render(
        request,
        "core/all_sales.html",
        {
            "sales": sales,
            "total_sales": total_sales,
            "total_items": total_items,
            "sales_count": sales_count,
            **profit_context,
            "store": store,
        },
    )


# ============================================================
# POS
# ============================================================

@login_required
def pos(request):

    store = get_current_store(request)

    if not store:

        if is_main_admin(request.user):
            return redirect(
                "manage_stores"
            )

        return render(
            request,
            "core/no_store.html",
        )

    query = request.GET.get(
        "q",
        "",
    ).strip()

    category = request.GET.get(
        "category",
        "",
    ).strip()

    products = (
        Product.objects
        .filter(
            store=store,
            active=True,
        )
        .select_related("category")
    )

    if query:

        products = products.filter(
            Q(name__icontains=query)
            | Q(barcode__icontains=query)
        )

    if category:

        products = products.filter(
            category__name=category
        )

    categories = [
        {
            "id": row["category_id"],
            "name": row["category__name"],
        }
        for row in (
            Product.objects
            .filter(
                store=store,
                active=True,
                category__isnull=False,
            )
            .values(
                "category_id",
                "category__name",
            )
            .distinct()
            .order_by(
                "category__name"
            )
        )
    ]

    return render(
        request,
        "core/pos.html",
        {
            "products": (
                products.order_by("name")
            ),
            "categories": categories,
            "selected_category": category,
            "query": query,
            "store": store,
        },
    )


@login_required
@require_POST
def sell_one(request, product_id):

    store = get_current_store(request)

    if not store:

        return JsonResponse(
            {
                "ok": False,
                "error": "No active store assigned.",
            },
            status=400,
        )

    with transaction.atomic():

        product = get_object_or_404(
            Product.objects.select_for_update(),
            pk=product_id,
            store=store,
            active=True,
        )

        if product.stock <= 0:

            return JsonResponse(
                {
                    "ok": False,
                    "error": "Out of stock",
                    "stock": 0,
                },
                status=400,
            )

        product.stock -= 1

        product.save(
            update_fields=[
                "stock"
            ]
        )

        Sale.objects.create(
            store=store,
            product=product,
            quantity=1,
            unit_price=product.selling_price,
            sold_by=request.user,
        )

        StockMovement.objects.create(
            product=product,
            store=store,
            movement_type="SALE",
            quantity=-1,
            user=request.user,
            note="POS sale",
        )

    return JsonResponse(
        {
            "ok": True,
            "stock": product.stock,
            "low_stock": product.is_low_stock,
            "message": (
                f"{product.name} sold"
            ),
        }
    )


# ============================================================
# RESTOCK
# ============================================================

@login_required
def restock(request):

    if not can_manage_stock(request.user):

        messages.error(
            request,
            (
                "You do not have permission "
                "to restock products."
            ),
        )

        return redirect(
            "dashboard"
        )

    store = get_current_store(request)

    if not store:

        if is_main_admin(request.user):
            return redirect(
                "manage_stores"
            )

        return render(
            request,
            "core/no_store.html",
        )

    if not has_product_management_access(store):

        messages.warning(
            request,
            (
                "Your 30-day free trial has expired. "
                "Please subscribe to continue managing products."
            ),
        )

        return redirect("subscription")

    products = (
        Product.objects
        .filter(
            store=store,
            active=True,
        )
        .order_by("name")
    )

    if request.method == "POST":

        product_id = request.POST.get(
            "product"
        )

        note = request.POST.get(
            "note",
            "",
        ).strip()

        try:

            quantity = int(
                request.POST.get(
                    "quantity"
                )
            )

            if quantity <= 0:
                raise ValueError

        except (
            TypeError,
            ValueError,
        ):

            messages.error(
                request,
                (
                    "Please enter a quantity "
                    "greater than zero."
                ),
            )

            return redirect(
                "restock"
            )

        with transaction.atomic():

            product = get_object_or_404(
                Product.objects.select_for_update(),
                id=product_id,
                store=store,
                active=True,
            )

            product.stock += quantity

            product.save(
                update_fields=[
                    "stock"
                ]
            )

            StockMovement.objects.create(
                product=product,
                store=store,
                movement_type="RESTOCK",
                quantity=quantity,
                user=request.user,
                note=note,
            )

        messages.success(
            request,
            (
                f"{product.name} restocked by "
                f"{quantity}. New stock: "
                f"{product.stock}"
            ),
        )

        return redirect(
            "restock"
        )

    return render(
        request,
        "core/restock.html",
        {
            "products": products,
            "store": store,
        },
    )


# ============================================================
# CART SALE
# ============================================================

@login_required
@require_POST
def process_cart_sale(request):

    # --------------------------------------------------------
    # READ JSON
    # --------------------------------------------------------

    try:
        payload = json.loads(
            request.body.decode("utf-8")
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):

        return JsonResponse(
            {
                "ok": False,
                "error": "Invalid sale data.",
            },
            status=400,
        )

    # --------------------------------------------------------
    # GET CART ITEMS
    # --------------------------------------------------------

    if not isinstance(payload, dict):

        return JsonResponse(
            {
                "ok": False,
                "error": "Invalid sale request.",
            },
            status=400,
        )

    cart_items = payload.get("items")

    if not isinstance(cart_items, list) or not cart_items:

        return JsonResponse(
            {
                "ok": False,
                "error": "Your cart is empty.",
            },
            status=400,
        )

    # --------------------------------------------------------
    # CLEAN AND COMBINE CART ITEMS
    # --------------------------------------------------------

    quantities = {}

    try:

        for item in cart_items:

            if not isinstance(item, dict):
                raise ValueError

            product_id = int(
                item.get("id")
            )

            quantity = int(
                item.get("quantity")
            )

            if product_id <= 0:
                raise ValueError

            if quantity <= 0:
                raise ValueError

            quantities[product_id] = (
                quantities.get(product_id, 0)
                + quantity
            )

    except (
        TypeError,
        ValueError,
    ):

        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "Each cart item must contain "
                    "a valid product and quantity."
                ),
            },
            status=400,
        )

    # --------------------------------------------------------
    # CURRENT STORE
    # --------------------------------------------------------

    store = get_current_store(request)

    if store is None:

        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "No active store is assigned "
                    "to your account."
                ),
            },
            status=400,
        )

    # --------------------------------------------------------
    # COMPLETE SALE ATOMICALLY
    # --------------------------------------------------------

    try:

        with transaction.atomic():

            product_ids = sorted(
                quantities.keys()
            )

            # Lock the products while processing
            # the transaction. This prevents two users
            # from selling the same remaining stock
            # simultaneously.

            products = list(
                Product.objects
                .select_for_update()
                .filter(
                    id__in=product_ids,
                    store=store,
                    active=True,
                )
                .order_by("id")
            )

            products_by_id = {
                product.id: product
                for product in products
            }

            # ------------------------------------------------
            # MAKE SURE EVERY PRODUCT EXISTS
            # ------------------------------------------------

            missing_products = [
                product_id
                for product_id in product_ids
                if product_id not in products_by_id
            ]

            if missing_products:

                return JsonResponse(
                    {
                        "ok": False,
                        "error": (
                            "One or more products are "
                            "no longer available."
                        ),
                    },
                    status=404,
                )

            # ------------------------------------------------
            # CHECK STOCK BEFORE MODIFYING ANYTHING
            # ------------------------------------------------

            for product_id in product_ids:

                product = products_by_id[
                    product_id
                ]

                requested_quantity = quantities[
                    product_id
                ]

                if product.stock < requested_quantity:

                    return JsonResponse(
                        {
                            "ok": False,
                            "error": (
                                f"Insufficient stock for "
                                f"{product.name}. "
                                f"Only {product.stock} "
                                f"remaining."
                            ),
                        },
                        status=400,
                    )

            # ------------------------------------------------
            # PROCESS EACH PRODUCT
            # ------------------------------------------------

            updates = []

            for product_id in product_ids:

                product = products_by_id[
                    product_id
                ]

                quantity = quantities[
                    product_id
                ]

                old_stock = product.stock

                # --------------------------------------------
                # REDUCE STOCK
                # --------------------------------------------

                product.stock = (
                    old_stock - quantity
                )

                product.save(
                    update_fields=[
                        "stock",
                    ]
                )

                # --------------------------------------------
                # CREATE SALE RECORD
                # --------------------------------------------

                unit_price = Decimal(product.selling_price)
                total_price = unit_price * quantity

                Sale.objects.create(
                    store=store,
                    product=product,
                    quantity=quantity,
                    unit_price=unit_price,
                    total_price=total_price,
                    sold_by=request.user,
                )

                # --------------------------------------------
                # CREATE STOCK MOVEMENT
                # --------------------------------------------

                StockMovement.objects.create(
                    product=product,
                    store=store,
                    movement_type="SALE",
                    quantity=-quantity,
                    user=request.user,
                    note="POS cart sale",
                )

                # --------------------------------------------
                # SEND UPDATED STOCK TO FRONTEND
                # --------------------------------------------

                updates.append(
                    {
                        "id": product.id,
                        "new_stock": product.stock,
                        "is_low": (
                            product.stock
                            <= product.low_stock_threshold
                        ),
                    }
                )

            # ------------------------------------------------
            # SUCCESS
            # ------------------------------------------------

            return JsonResponse(
                {
                    "ok": True,
                    "message": "Sale completed successfully.",
                    "updates": updates,
                },
                status=200,
            )

    # --------------------------------------------------------
    # DATABASE / UNEXPECTED ERROR
    # --------------------------------------------------------

    except Exception as exc:

        logger.exception(
            "POS cart sale failed: %s",
            exc,
        )

        # During development, return the actual error so
        # the browser can tell us exactly what is wrong.
        if settings.DEBUG:

            return JsonResponse(
                {
                    "ok": False,
                    "error": str(exc),
                },
                status=500,
            )

        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "The sale could not be completed. "
                    "Please try again."
                ),
            },
            status=500,
        )


# ============================================================
# ADD PRODUCT
# ============================================================

@login_required
def add_product(request):

    # --------------------------------------------------------
    # PERMISSION
    # --------------------------------------------------------

    if not can_manage_products(request.user):

        messages.error(
            request,
            "You do not have permission to add products.",
        )

        return redirect("pos")

    # --------------------------------------------------------
    # STORE
    # --------------------------------------------------------

    store = get_current_store(request)

    if store is None:

        messages.error(
            request,
            "No active store is assigned to your account.",
        )

        return redirect("dashboard")

    # --------------------------------------------------------
    # SUBSCRIPTION RESTRICTION
    # --------------------------------------------------------

    if not has_product_management_access(store):

        messages.warning(
            request,
            (
                "Your 30-day free trial has expired. "
                "Please subscribe to continue adding products."
            ),
        )

        return redirect("subscription")

    # --------------------------------------------------------
    # STORE-SPECIFIC CATEGORIES
    # --------------------------------------------------------

    categories = (
        Category.objects
        .filter(store=store)
        .order_by("name")
    )

    # --------------------------------------------------------
    # CONTEXT HELPER
    # --------------------------------------------------------

    def page_context():
        return {
            "categories": categories,
            "store": store,
            "subscription": subscription_status(store),
        }

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    if request.method == "POST":

        name = request.POST.get(
            "name",
            "",
        ).strip()

        category_id = request.POST.get(
            "category",
            "",
        ).strip()

        preferred_category = request.POST.get(
            "preferred_category",
            "",
        ).strip()

        selling_price = request.POST.get(
            "selling_price",
            "",
        ).strip()

        cost_price = request.POST.get(
            "cost_price",
            "",
        ).strip()

        stock = request.POST.get(
            "stock",
            "0",
        ).strip()

        low_stock_threshold = request.POST.get(
            "low_stock_threshold",
            "20",
        ).strip()

        barcode = request.POST.get(
            "barcode",
            "",
        ).strip()

        active = (
            request.POST.get("active") == "on"
        )

        save_and_add_another = (
            request.POST.get(
                "save_and_add_another"
            )
            == "1"
        )

        # ----------------------------------------------------
        # NAME
        # ----------------------------------------------------

        if not name:

            messages.error(
                request,
                "Please enter the product name.",
            )

            return render(
                request,
                "core/add_product.html",
                page_context(),
            )

        # ----------------------------------------------------
        # SELLING PRICE REQUIRED
        # ----------------------------------------------------

        if not selling_price:

            messages.error(
                request,
                "Please enter the selling price.",
            )

            return render(
                request,
                "core/add_product.html",
                page_context(),
            )

        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        category = None

        # ----------------------------------------------------
        # PREFERRED / NEW CATEGORY
        # ----------------------------------------------------

        if preferred_category:

            # Prevent duplicate category names within
            # the same store.
            category, _ = (
                Category.objects.get_or_create(
                    store=store,
                    name=preferred_category,
                )
            )

        # ----------------------------------------------------
        # EXISTING CATEGORY
        # ----------------------------------------------------

        elif category_id:

            # IMPORTANT:
            # The category MUST belong to this store.
            #
            # This prevents a user from manually submitting
            # another store's category ID.

            category = get_object_or_404(
                Category,
                id=category_id,
                store=store,
            )

        # ----------------------------------------------------
        # SELLING PRICE
        # ----------------------------------------------------

        try:

            selling_price_decimal = Decimal(
                selling_price
            )

            if selling_price_decimal < 0:
                raise ValueError

        except (
            InvalidOperation,
            ValueError,
        ):

            messages.error(
                request,
                "Please enter a valid selling price.",
            )

            return render(
                request,
                "core/add_product.html",
                page_context(),
            )

        # ----------------------------------------------------
        # COST PRICE
        # ----------------------------------------------------

        cost_price_decimal = None

        if cost_price:

            try:

                cost_price_decimal = Decimal(
                    cost_price
                )

                if cost_price_decimal < 0:
                    raise ValueError

            except (
                InvalidOperation,
                ValueError,
            ):

                messages.error(
                    request,
                    "Please enter a valid cost price.",
                )

                return render(
                    request,
                    "core/add_product.html",
                    page_context(),
                )

        # ----------------------------------------------------
        # STOCK
        # ----------------------------------------------------

        try:

            stock_value = int(
                stock
            )

            if stock_value < 0:
                raise ValueError

        except (
            TypeError,
            ValueError,
        ):

            messages.error(
                request,
                "Stock must be a valid number.",
            )

            return render(
                request,
                "core/add_product.html",
                page_context(),
            )

        # ----------------------------------------------------
        # LOW STOCK THRESHOLD
        # ----------------------------------------------------

        try:

            low_stock_value = int(
                low_stock_threshold
            )

            if low_stock_value < 0:
                raise ValueError

        except (
            TypeError,
            ValueError,
        ):

            messages.error(
                request,
                "Low-stock threshold must be a valid number.",
            )

            return render(
                request,
                "core/add_product.html",
                page_context(),
            )

        # ----------------------------------------------------
        # CREATE PRODUCT
        # ----------------------------------------------------

        Product.objects.create(
            store=store,
            category=category,
            name=name,
            selling_price=selling_price_decimal,
            cost_price=cost_price_decimal,
            stock=stock_value,
            low_stock_threshold=low_stock_value,
            barcode=barcode or None,
            active=active,
        )

        messages.success(
            request,
            f'"{name}" was added successfully.',
        )

        # ----------------------------------------------------
        # SAVE AND ADD ANOTHER
        # ----------------------------------------------------

        if save_and_add_another:

            return redirect(
                "add_product"
            )

        return redirect(
            "pos"
        )

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    return render(
        request,
        "core/add_product.html",
        page_context(),
    )


@login_required
def edit_product(request, product_id):

    # --------------------------------------------------------
    # PERMISSION
    # --------------------------------------------------------

    if not can_manage_products(request.user):

        messages.error(
            request,
            "You do not have permission to edit products.",
        )

        return redirect("active_products")

    # --------------------------------------------------------
    # STORE
    # --------------------------------------------------------

    store = get_current_store(request)

    if store is None:

        messages.error(
            request,
            "No active store is assigned to your account.",
        )

        return redirect("dashboard")

    # --------------------------------------------------------
    # SUBSCRIPTION RESTRICTION
    # --------------------------------------------------------

    if not has_product_management_access(store):

        messages.warning(
            request,
            (
                "Your 30-day free trial has expired. "
                "Please subscribe to continue managing products."
            ),
        )

        return redirect("subscription")

    # --------------------------------------------------------
    # PRODUCT
    # --------------------------------------------------------

    product = get_object_or_404(
        Product,
        id=product_id,
        store=store,
    )

    # --------------------------------------------------------
    # STORE-SPECIFIC CATEGORIES
    # --------------------------------------------------------

    categories = (
        Category.objects
        .filter(store=store)
        .order_by("name")
    )

    # --------------------------------------------------------
    # CONTEXT HELPER
    # --------------------------------------------------------

    def page_context():

        return {
            "product": product,
            "categories": categories,
            "store": store,
            "subscription": subscription_status(store),
        }

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    if request.method == "POST":

        name = request.POST.get(
            "name",
            "",
        ).strip()

        category_id = request.POST.get(
            "category",
            "",
        ).strip()

        preferred_category = request.POST.get(
            "preferred_category",
            "",
        ).strip()

        selling_price = request.POST.get(
            "selling_price",
            "",
        ).strip()

        cost_price = request.POST.get(
            "cost_price",
            "",
        ).strip()

        stock = request.POST.get(
            "stock",
            "0",
        ).strip()

        low_stock_threshold = request.POST.get(
            "low_stock_threshold",
            "20",
        ).strip()

        barcode = request.POST.get(
            "barcode",
            "",
        ).strip()

        active = product.active

        # ----------------------------------------------------
        # NAME
        # ----------------------------------------------------

        if not name:

            messages.error(
                request,
                "Please enter the product name.",
            )

            return render(
                request,
                "core/edit_product.html",
                page_context(),
            )

        # ----------------------------------------------------
        # SELLING PRICE REQUIRED
        # ----------------------------------------------------

        if not selling_price:

            messages.error(
                request,
                "Please enter the selling price.",
            )

            return render(
                request,
                "core/edit_product.html",
                page_context(),
            )

        # ----------------------------------------------------
        # CATEGORY
        # ----------------------------------------------------

        category = None

        # ----------------------------------------------------
        # PREFERRED / NEW CATEGORY
        # ----------------------------------------------------

        if preferred_category:

            category, _ = (
                Category.objects.get_or_create(
                    store=store,
                    name=preferred_category,
                )
            )

        # ----------------------------------------------------
        # EXISTING CATEGORY
        # ----------------------------------------------------

        elif category_id:

            category = get_object_or_404(
                Category,
                id=category_id,
                store=store,
            )

        # ----------------------------------------------------
        # SELLING PRICE
        # ----------------------------------------------------

        try:

            selling_price_decimal = Decimal(
                selling_price
            )

            if selling_price_decimal < 0:
                raise ValueError

        except (
            InvalidOperation,
            ValueError,
        ):

            messages.error(
                request,
                "Please enter a valid selling price.",
            )

            return render(
                request,
                "core/edit_product.html",
                page_context(),
            )

        # ----------------------------------------------------
        # COST PRICE
        # ----------------------------------------------------

        cost_price_decimal = None

        if cost_price:

            try:

                cost_price_decimal = Decimal(
                    cost_price
                )

                if cost_price_decimal < 0:
                    raise ValueError

            except (
                InvalidOperation,
                ValueError,
            ):

                messages.error(
                    request,
                    "Please enter a valid cost price.",
                )

                return render(
                    request,
                    "core/edit_product.html",
                    page_context(),
                )

        # ----------------------------------------------------
        # STOCK
        # ----------------------------------------------------

        try:

            stock_value = int(
                stock
            )

            if stock_value < 0:
                raise ValueError

        except (
            TypeError,
            ValueError,
        ):

            messages.error(
                request,
                "Stock must be a valid number.",
            )

            return render(
                request,
                "core/edit_product.html",
                page_context(),
            )

        # ----------------------------------------------------
        # LOW STOCK THRESHOLD
        # ----------------------------------------------------

        try:

            low_stock_value = int(
                low_stock_threshold
            )

            if low_stock_value < 0:
                raise ValueError

        except (
            TypeError,
            ValueError,
        ):

            messages.error(
                request,
                "Low-stock threshold must be a valid number.",
            )

            return render(
                request,
                "core/edit_product.html",
                page_context(),
            )

        # ----------------------------------------------------
        # UPDATE PRODUCT
        # ----------------------------------------------------

        product.category = category
        product.name = name
        product.selling_price = selling_price_decimal
        product.cost_price = cost_price_decimal
        product.stock = stock_value
        product.low_stock_threshold = low_stock_value
        product.barcode = barcode or ""
        product.active = active

        product.save()

        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        messages.success(
            request,
            f'"{product.name}" was updated successfully.',
        )

        return redirect(
            "active_products"
        )

    # --------------------------------------------------------
    # GET
    # --------------------------------------------------------

    return render(
        request,
        "core/edit_product.html",
        page_context(),
    )


# ============================================================
# SUBSCRIPTION PAGE
# ============================================================

@login_required
def subscription(request):

    store = get_current_store(request)

    if not store:

        if is_main_admin(request.user):
            return redirect(
                "manage_stores"
            )

        return render(
            request,
            "core/no_store.html",
        )

    status = subscription_status(
        store
    )

    subscriptions = (
        Subscription.objects
        .filter(
            store=store
        )
        .order_by(
            "-created_at"
        )
    )

    return render(
        request,
        "core/subscription.html",
        {
            "store": store,
            "subscription": status,
            "subscriptions": subscriptions,
            "subscription_plans": (
                SUBSCRIPTION_PLANS
            ),
        },
    )


# ============================================================
# LOGOUT
# ============================================================

def custom_logout_view(request):

    auth_logout(
        request
    )

    return redirect(
        "login"
    )


# ============================================================
# STORE ATTENDANT ACCOUNT CREATION
# ============================================================

@login_required
@require_POST
def create_attendant(request):
    """Create an attendant for the signed-in manager's active store."""
    if not (
        request.user.is_active
        and (
            request.user.is_superuser
            or request.user.role in ["ADMIN", "MANAGER"]
        )
    ):
        return HttpResponse(
            "You do not have permission to create attendant accounts.",
            status=403,
        )

    store = get_current_store(request)
    if store is None:
        messages.error(
            request,
            "No active store is assigned to your account.",
            extra_tags="attendant",
        )
        return redirect("dashboard")

    dashboard_url = reverse("dashboard") + "#attendant-accounts"
    username = User.normalize_username(
        request.POST.get("username", "").strip()
    )
    password = request.POST.get("password1", "")
    password_confirmation = request.POST.get("password2", "")

    # The store and role come from the server, never from submitted fields.
    attendant = User(
        username=username,
        first_name=request.POST.get("first_name", "").strip(),
        last_name=request.POST.get("last_name", "").strip(),
        email=User.objects.normalize_email(
            request.POST.get("email", "").strip()
        ),
        role="ATTENDANT",
        store=store,
        is_active=True,
        is_staff=False,
        is_superuser=False,
    )

    errors = []
    if not username:
        errors.append("Please enter a username.")
    elif User.objects.filter(username__iexact=username).exists():
        errors.append(
            "This username is already taken. Choose another username.")

    if not password:
        errors.append("Please enter a password.")
    elif password != password_confirmation:
        errors.append("The passwords do not match.")

    try:
        attendant.full_clean(exclude=["password"])
    except ValidationError as error:
        errors.extend(error.messages)

    if password:
        try:
            validate_password(password, user=attendant)
        except ValidationError as error:
            errors.extend(error.messages)

    if errors:
        for error in dict.fromkeys(errors):
            messages.error(request, error, extra_tags="attendant")
        return redirect(dashboard_url)

    # Store a password hash, never the original password.
    attendant.set_password(password)
    try:
        with transaction.atomic():
            attendant.save()
    except IntegrityError:
        logger.exception("Unable to create attendant for store %s", store.pk)
        messages.error(
            request,
            "The account could not be created. The username may have just "
            "been taken. Choose another username and try again.",
            extra_tags="attendant",
        )
        return redirect(dashboard_url)

    messages.success(
        request,
        f"Attendant account '{attendant.username}' created successfully "
        f"for {store.name}. The attendant can sign in using the normal login page.",
        extra_tags="attendant",
    )
    return redirect(dashboard_url)


# ============================================================
# STORE ATTENDANT ACCOUNT MANAGEMENT
# ============================================================

@login_required
@require_POST
def manage_attendant(request, user_id):
    """Manage only ordinary attendant accounts in the manager's own store."""
    if not (
        request.user.is_active
        and (request.user.is_superuser or request.user.role in ["ADMIN", "MANAGER"])
    ):
        return HttpResponse("You do not have permission to manage attendants.", status=403)

    store = get_current_store(request)
    if store is None:
        messages.error(
            request, "No active store is assigned to your account.", extra_tags="attendant")
        return redirect("dashboard")

    destination = reverse("dashboard") + "#attendant-manage"
    action = request.POST.get("action", "")
    if action not in ["edit", "password", "activate", "deactivate", "delete"]:
        return HttpResponse("Invalid attendant action.", status=400)

    try:
        with transaction.atomic():
            attendant = get_object_or_404(
                User.objects.select_for_update(),
                pk=user_id, store=store, role="ATTENDANT",
                is_superuser=False, is_staff=False,
            )

            if action == "edit":
                attendant.first_name = request.POST.get(
                    "first_name", "").strip()
                attendant.last_name = request.POST.get("last_name", "").strip()
                attendant.email = User.objects.normalize_email(
                    request.POST.get("email", "").strip())
                attendant.full_clean(exclude=["password"])
                attendant.save(
                    update_fields=["first_name", "last_name", "email"])
                feedback = f"Details updated for '{attendant.username}'."

            elif action == "password":
                password = request.POST.get("password1", "")
                confirmation = request.POST.get("password2", "")
                if not password:
                    raise ValidationError("Please enter a new password.")
                if password != confirmation:
                    raise ValidationError("The passwords do not match.")
                validate_password(password, user=attendant)
                attendant.set_password(password)
                attendant.save(update_fields=["password"])
                feedback = f"Password reset for '{attendant.username}'."

            elif action == "delete":
                username = attendant.username
                attendant.delete()
                feedback = f"Attendant '{username}' deleted successfully."

            else:
                attendant.is_active = action == "activate"
                attendant.save(update_fields=["is_active"])
                status = "activated" if attendant.is_active else "deactivated"
                feedback = f"Attendant '{attendant.username}' {status}."

    except ValidationError as error:
        for text in error.messages:
            messages.error(request, text, extra_tags="attendant")
        return redirect(destination)
    except IntegrityError:
        logger.exception(
            "Unable to update attendant %s in store %s", user_id, store.pk)
        messages.error(
            request, "The attendant account could not be updated. Please try again.", extra_tags="attendant")
        return redirect(destination)

    messages.success(request, feedback, extra_tags="attendant")
    return redirect(destination)
