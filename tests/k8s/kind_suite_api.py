"""``K8sApiPort`` factory of the kind provider suites (kubernetes-deployment task 6.23).

``tests/labs/sandbox_backend.py`` reads ``EDU_IT_LAB_SUITE_K8S_API=<module>:<callable>`` and calls
it with no argument. ``kind_api`` answers with the adapter bound to the kind context
(``EDU_IT_KIND_CONTEXT``, default ``kind-edu-it``), the same one the 6.22 contract test uses.

``EDU_IT_KIND_ADAPTER`` selects the adapter: ``client`` (default, the production adapter over the
official Kubernetes client, ``edu_it_infrastructure.sandbox.k8s_client``) or ``kubectl`` (the
original ``kubectl`` harness adapter).
"""

from __future__ import annotations

import os

from edu_it_infrastructure.sandbox.k8s_api import K8sApiPort

KIND_CONTEXT_ENV = "EDU_IT_KIND_CONTEXT"
KIND_ADAPTER_ENV = "EDU_IT_KIND_ADAPTER"
DEFAULT_KIND_CONTEXT = "kind-edu-it"


def kind_api() -> K8sApiPort:
    context = os.environ.get(KIND_CONTEXT_ENV, DEFAULT_KIND_CONTEXT)
    adapter = os.environ.get(KIND_ADAPTER_ENV, "client")
    if adapter == "kubectl":
        from tests.k8s.kubectl_api import KubectlK8sApi

        return KubectlK8sApi(context)
    if adapter != "client":
        raise ValueError(f"{KIND_ADAPTER_ENV} must be 'client' or 'kubectl', not {adapter!r}")
    from edu_it_infrastructure.sandbox.k8s_client import build_k8s_api_from_kubeconfig

    return build_k8s_api_from_kubeconfig(context)
