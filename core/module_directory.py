"""A directory of the registry-driven service modules.

Phase 7 adds a family of small department apps (library, health, sports and
so on). They all expose the same registry-driven screens, so one page can
list the ones this user may open instead of adding a dozen links to the
sidebar.
"""

from importlib import import_module

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse

# (app label, URL namespace). The module code and feature flag come from the
# registry itself, because a few differ from the app label.
DIRECT_LINKS = [
    {
        "title": "Teacher-safe health alerts",
        "description": "Health notes a nurse has cleared to share with teachers.",
        "url_name": "health:safe_alerts",
        "permission": "health.view_safe_alerts",
        "flag": "health",
        "sensitive": True,
    },
]

SERVICE_APPS = [
    ("library", "library"),
    ("health", "health"),
    ("counselling", "counselling"),
    ("student_affairs", "student_affairs"),
    ("sports", "sports"),
    ("boarding", "boarding"),
    ("transport", "transport"),
    ("catering", "catering"),
    ("careers", "careers"),
    ("examinations", "examinations"),
    ("research", "research"),
    ("labs", "labs"),
    ("ict_helpdesk", "ict_helpdesk"),
]


def service_modules():
    """Every service module definition, in a stable order."""
    modules = []
    for app_name, namespace in SERVICE_APPS:
        registry = import_module(f"{app_name}.registry").MODULES
        for code, module in registry.items():
            modules.append((code, namespace, module))
    return modules


@login_required
def module_directory(request):
    """List the service modules this user may open."""
    from core.access import user_has_any_code, user_has_code
    from core.modules import is_enabled

    institution = getattr(request, "institution", None)
    if institution is None:
        institution = getattr(request.user, "institution", None)
    entries = []
    for code, namespace, module in service_modules():
        flag = module.get("flag", code)
        if not is_enabled(flag, institution):
            continue
        if not user_has_any_code(request.user, module["permissions"]):
            continue
        entries.append(
            {
                "code": code,
                "title": module["title"],
                "description": module.get("description", ""),
                "url": reverse(f"{namespace}:hub"),
                "sensitive": module.get("sensitive", False),
            }
        )
    for link in DIRECT_LINKS:
        if not is_enabled(link["flag"], institution):
            continue
        if not user_has_code(request.user, link["permission"]):
            continue
        entries.append(
            {
                "code": link["url_name"],
                "title": link["title"],
                "description": link["description"],
                "url": reverse(link["url_name"]),
                "sensitive": link.get("sensitive", False),
            }
        )
    entries.sort(key=lambda entry: entry["title"])
    return render(request, "core/module_directory.html", {"modules": entries})
