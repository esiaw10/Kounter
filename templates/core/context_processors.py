from .views import subscription_status


def subscription_context(request):
    """
    Makes subscription information available to the navbar
    and every template rendered by Django.
    """

    if not request.user.is_authenticated:
        return {
            "subscription": None,
        }

    if request.user.is_superuser:
        return {
            "subscription": None,
        }

    store = getattr(request.user, "store", None)

    if not store:
        return {
            "subscription": None,
        }

    return {
        "subscription": subscription_status(store),
    }


```
