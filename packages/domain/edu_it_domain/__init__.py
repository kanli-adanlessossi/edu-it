"""EDU-IT framework-free domain layer.

Ports, business config, and entities reused by the application and
infrastructure layers. This package imports no web or API framework
(Req 1.8, ``.kiro/steering/structure.md``).

The external-system ports (``.kiro/steering/tech.md``) are re-exported here for
convenience: ``Clock``, ``Mailer``, ``ObjectStorage``, ``TaskQueue``, and
``LLMProvider``, together with their supporting value objects.
"""

from edu_it_domain.ports import (
    Clock,
    JobHandler,
    LLMCompletion,
    LLMProvider,
    Mailer,
    ObjectStorage,
    OutboundMail,
    StoredObject,
    TaskQueue,
)

__all__ = [
    "Clock",
    "JobHandler",
    "LLMCompletion",
    "LLMProvider",
    "Mailer",
    "ObjectStorage",
    "OutboundMail",
    "StoredObject",
    "TaskQueue",
    "__version__",
]

__version__ = "0.0.0"
