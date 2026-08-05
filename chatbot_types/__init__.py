from .ecommerce import Ecommerce
from .general import General
from .job_posting import Job_posting
from .services import Services
from .restaurant import Restaurant

CLASSES = {
    Ecommerce.name: Ecommerce,
    General.name: General,
    Job_posting.name: Job_posting,
    Services.name: Services,
    Restaurant.name: Restaurant,
}


def available_classes():
    """List handler classes for the chatbot type CRUD dropdown."""
    return [
        {"value": name, "label": getattr(cls, "label", name)}
        for name, cls in CLASSES.items()
    ]


def get_class(name):
    return CLASSES.get(name)


from .routes import chatbot_types_bp, ensure_schema  # noqa: E402
