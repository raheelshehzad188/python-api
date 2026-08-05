from .custom_laravel import Custom_laravel

# Registry of available ecommerce integration classes, keyed by the value
# stored in sub_categories.ecommerce_class.
CLASSES = {
    Custom_laravel.name: Custom_laravel,
}


def available_classes():
    """List of class info for the frontend dropdown."""
    return [{"value": name, "label": getattr(cls, "label", name)} for name, cls in CLASSES.items()]


def get_class(name):
    return CLASSES.get(name)
