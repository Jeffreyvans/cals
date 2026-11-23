"""Template helpers for the shared academic hierarchy filters."""

from django import template

from academics.hierarchy import (
    breadcrumb_label,
    hierarchy_filter_context,
    hierarchy_label,
)

register = template.Library()


@register.simple_tag(takes_context=True)
def hierarchy_crumb(context):
    """The selected school/department/level/class, shown in the top bar."""
    request = context.get("request")
    return breadcrumb_label(request) if request is not None else ""


@register.inclusion_tag("academics/_hierarchy_filters.html", takes_context=True)
def hierarchy_filters(context, hide_department=False):
    """Render the four filter selects, keeping other GET parameters."""
    request = context.get("request")
    result = hierarchy_filter_context(request) if request is not None else {}
    result["hide_department"] = hide_department
    return result


@register.simple_tag
def hierarchy_label_for(obj):
    """Show a student's school/faculty and department on screen and in prints."""
    return hierarchy_label(obj) if obj is not None else ""
