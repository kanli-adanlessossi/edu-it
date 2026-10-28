"""EDU-IT infrastructure layer.

Adapters implementing the domain ports against real external systems. May
depend on frameworks (Pydantic v2); the domain may not
(``.kiro/steering/structure.md``).

The production port adapters live here beside the ``fakes`` subpackage: the
system :class:`~edu_it_infrastructure.clock.System_Clock` and the SMTP
:class:`~edu_it_infrastructure.mailer.SMTP_Mailer`. The
:mod:`~edu_it_infrastructure.composition` composition root selects production
adapters or the test/dev fakes per environment.
"""

from edu_it_infrastructure.clock import System_Clock
from edu_it_infrastructure.composition import (
    CompositionSettings,
    Environment,
    MailerBackend,
    Ports,
    build_ports,
)
from edu_it_infrastructure.datastore_errors import (
    DATASTORE_UNAVAILABLE_ERRORS,
    is_datastore_unavailable,
)
from edu_it_infrastructure.mailer import SMTP_DeliveryError, SMTP_Mailer, SMTPConfig

__all__ = [
    "DATASTORE_UNAVAILABLE_ERRORS",
    "CompositionSettings",
    "Environment",
    "MailerBackend",
    "Ports",
    "SMTPConfig",
    "SMTP_DeliveryError",
    "SMTP_Mailer",
    "System_Clock",
    "__version__",
    "build_ports",
    "is_datastore_unavailable",
]

__version__ = "0.0.0"
