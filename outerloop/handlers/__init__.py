"""Handler registry. Adding a 4th ticket type = one subclass + one line here."""

from .coding import CodingHandler
from .knowledge import KnowledgeHandler
from .ops import OpsHandler

HANDLER_REGISTRY = {
    "coding": CodingHandler(),
    "knowledge": KnowledgeHandler(),
    "ops": OpsHandler(),
}


def get_handler(ticket_type):
    return HANDLER_REGISTRY[ticket_type]
