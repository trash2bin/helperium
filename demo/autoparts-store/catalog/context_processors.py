"""Template context for the standalone public storefront."""
from django.conf import settings

from .models import Cart, Category, SiteSettings


def site_settings(request):
    configuration = SiteSettings.objects.first()
    cart_count = 0
    if request.session.session_key:
        cart = Cart.objects.filter(session_key=request.session.session_key).first()
        if cart:
            cart_count = cart.items.count()
    return {
        "site_settings": configuration,
        "categories": Category.objects.filter(parent__isnull=True, is_active=True),
        "cart_count": cart_count,
    }


def demo_runtime(request):
    return {
        "assistant_enabled": settings.HELPERIUM_WIDGET_ENABLED,
        "assistant_api_base": settings.HELPERIUM_API_BASE,
        "assistant_agent": settings.HELPERIUM_AGENT,
        "assistant_title": settings.HELPERIUM_WIDGET_TITLE,
        "demo_order_submissions": settings.DEMO_ORDER_SUBMISSIONS,
    }
