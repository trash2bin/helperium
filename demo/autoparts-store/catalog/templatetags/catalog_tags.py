from django import template
from catalog.models import Category, Brand

register = template.Library()

@register.simple_tag
def get_categories():
    return Category.objects.filter(is_active=True, parent__isnull=True).prefetch_related('children')

@register.filter
def ru_plural(value, arg):
    """1 товар, 2 товара, 5 товаров"""
    variants = arg.split(',')
    if len(variants) != 3:
        return str(value)
    try:
        v = abs(int(value)) % 100
        n = v % 10
        if v > 10 and v < 20:
            return f'{value} {variants[2]}'
        if n > 1 and n < 5:
            return f'{value} {variants[1]}'
        if n == 1:
            return f'{value} {variants[0]}'
        return f'{value} {variants[2]}'
    except (ValueError, TypeError):
        return str(value)
