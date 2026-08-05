from catalog.models import SiteSettings, Cart

def site_settings(request):
    settings = SiteSettings.get_settings()
    cart = None
    cart_count = 0
    cart_total = 0
    if request.session.session_key:
        try:
            cart = Cart.objects.get(session_key=request.session.session_key)
            cart_count = cart.total_items
            cart_total = cart.total_price
        except Cart.DoesNotExist:
            pass
    return {
        'site_settings': settings,
        'cart_count': cart_count,
        'cart_total': cart_total,
    }
