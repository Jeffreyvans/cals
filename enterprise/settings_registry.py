"""The shared settings a group may define for its campuses.

Adding a key here does not rewrite any campus record: a shared setting is a
small JSON configuration row. A campus can hold its own override unless the
group locked the key, and issued documents keep the layout they were issued
with. The registry is the allow-list used by forms and services, so a request
cannot invent a new setting key.
"""

SHARED_SETTINGS = {
    "grading_scale": "Grading scale",
    "fee_item_templates": "Fee item templates",
    "academic_calendar": "Academic calendar",
    "terminology": "Terminology",
    "report_card_layout": "Report card layout",
    "policies": "Policies",
    "document_templates": "Document templates",
    "roles": "Roles",
}

SETTING_KEYS = tuple(SHARED_SETTINGS)


def label(key):
    """The human name of a registered shared setting."""
    return SHARED_SETTINGS.get(key, key)
