from .models import Store


def subscription_context(request):
    """
    Makes subscription information available to all templates
    for the currently logged-in user's store.
    """

    context = {
        "subscription_info": None,
        "subscription_status": None,
        "subscription_days_remaining": 0,
        "subscription_can_add_products": False,
        "subscription_active": False,
        "trial_active": False,
    }

    if not request.user.is_authenticated:
        return context

    store = getattr(request.user, "store", None)

    if not store:
        return context

    # Active paid subscription
    if store.subscription_active:
        subscription = store.active_subscription

        context.update({
            "subscription_info": subscription,
            "subscription_status": "ACTIVE",
            "subscription_days_remaining": subscription.days_remaining,
            "subscription_can_add_products": True,
            "subscription_active": True,
            "trial_active": False,
        })

        return context

    # 30-day free trial
    if store.trial_active:
        context.update({
            "subscription_info": None,
            "subscription_status": "TRIAL",
            "subscription_days_remaining": store.trial_days_remaining,
            "subscription_can_add_products": True,
            "subscription_active": False,
            "trial_active": True,
        })

        return context

    # Trial expired and no active subscription
    context.update({
        "subscription_info": None,
        "subscription_status": "EXPIRED",
        "subscription_days_remaining": 0,
        "subscription_can_add_products": False,
        "subscription_active": False,
        "trial_active": False,
    })

    return context
