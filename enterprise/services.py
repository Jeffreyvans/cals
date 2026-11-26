"""Business rules for groups of schools.

Every figure on a group dashboard is calculated from the campus's own rows at
read time. No campus record is copied into the group and no student name is
included in an aggregate.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.email_types import BILLING_REMINDER
from core.notifications import send_email
from core.tenancy import use_institution

# ---------- organisation structure ----------


@transaction.atomic
def create_organisation(*, name, slug, org_type, actor=None, **fields):
    from enterprise.models import Organisation

    allowed = {
        "logo", "primary_color", "accent_color", "contact_email", "contact_phone",
        "address", "website", "status",
    }
    org = Organisation.objects.create(
        name=name, slug=slug, type=org_type,
        **{k: v for k, v in fields.items() if k in allowed},
    )
    from core.audit import log_action

    log_action(
        actor, "group.organisation_created", instance=org,
        after={"name": org.name, "slug": org.slug, "type": org.type},
    )
    return org


@transaction.atomic
def attach_institution(organisation, institution, actor=None, campus_label="", sort_order=0):
    """Attach an existing institution. Nothing in the institution is changed."""
    from enterprise.models import OrganisationMembership

    current = OrganisationMembership.objects.filter(institution=institution).first()
    if current is not None and current.organisation_id != organisation.pk:
        raise ValidationError(
            "This campus is already attached to another organisation. "
            "Detach it there first; no data is deleted by detaching."
        )
    membership, _ = OrganisationMembership.objects.update_or_create(
        institution=institution,
        defaults={
            "organisation": organisation,
            "campus_label": campus_label or institution.name,
            "sort_order": int(sort_order or 0),
            "attached_by": actor,
        },
    )
    from core.audit import log_action

    log_action(
        actor, "group.campus_attached", instance=institution,
        after={"organisation_id": organisation.pk, "campus": membership.campus_label},
    )
    return membership


@transaction.atomic
def detach_institution(organisation, institution, actor=None):
    """Remove the link only. Students, money and documents stay untouched."""
    from enterprise.models import OrganisationMembership

    membership = OrganisationMembership.objects.filter(
        organisation=organisation, institution=institution
    ).first()
    if membership is None:
        return False
    # Overrides stay in place: if the campus is reattached to the same group,
    # its local choices are still there. They are scoped to the organisation,
    # so another group never inherits them. Nothing is deleted.
    membership.delete()
    from core.audit import log_action

    log_action(
        actor, "group.campus_detached", instance=institution,
        after={"organisation_id": organisation.pk},
    )
    return True


@transaction.atomic
def assign_group_role(organisation, user, role, actor=None):
    from enterprise.access import ALL_GROUP_ROLES
    from enterprise.models import GroupMembership

    if role not in ALL_GROUP_ROLES:
        raise ValidationError("Choose a recognised group role.")
    membership, _ = GroupMembership.objects.update_or_create(
        organisation=organisation, user=user, defaults={"role": role, "is_active": True}
    )
    from core.audit import log_action

    log_action(
        actor, "group.role_assigned", instance=membership,
        after={"organisation_id": organisation.pk, "role": role, "user_id": user.pk},
    )
    return membership


def revoke_group_role(organisation, user, actor=None):
    from enterprise.models import GroupMembership

    updated = GroupMembership.objects.filter(
        organisation=organisation, user=user, is_active=True
    ).update(is_active=False)
    from core.audit import log_action

    log_action(
        actor, "group.role_revoked", after={"organisation_id": organisation.pk,
                                             "user_id": user.pk}
    )
    return bool(updated)


# ---------- shared settings ----------


def effective_setting(organisation, institution, key):
    """The value a campus actually uses and where it came from.

    Returns ``{"value", "source", "label", "locked"}``. A locked group default
    always wins; otherwise a campus override wins. Nothing is written here, so
    changing a group default cannot rewrite an existing campus record.
    """
    from enterprise.models import CampusSettingOverride, SharedSetting
    from enterprise.settings_registry import SHARED_SETTINGS

    if key not in SHARED_SETTINGS:
        raise ValidationError("Unknown shared setting.")
    shared = SharedSetting.objects.filter(organisation=organisation, key=key).first()
    override = None
    if institution is not None:
        # A campus keeps its old override row when it is detached so reattaching
        # it to the same group is harmless. It must not be used while the campus
        # belongs to a different organisation.
        from enterprise.models import OrganisationMembership

        still_a_member = OrganisationMembership.objects.filter(
            organisation=organisation, institution=institution
        ).exists()
        override = CampusSettingOverride.objects.filter(
            organisation=organisation, institution=institution, key=key
        ).first() if still_a_member else None
    if shared is not None and shared.locked:
        return {
            "value": shared.value, "source": "group", "label": "Inherited from group",
            "locked": True, "key": key,
        }
    if override is not None:
        return {
            "value": override.value, "source": "campus", "label": "Set by this campus",
            "locked": False, "key": key,
        }
    if shared is not None:
        return {
            "value": shared.value, "source": "group", "label": "Inherited from group",
            "locked": False, "key": key,
        }
    return {"value": {}, "source": "default", "label": "Platform default",
            "locked": False, "key": key}


def setting_preview(organisation, key, force_lock=False):
    """Which campuses would change if a group default were saved.

    ``force_lock`` reflects the lock checkbox on the form: a locked default
    replaces even a campus's own override.
    """
    from enterprise.models import CampusSettingOverride, SharedSetting

    campuses = organisation.campuses()
    locked = force_lock or SharedSetting.objects.filter(
        organisation=organisation, key=key, locked=True
    ).exists()
    overridden = set(
        CampusSettingOverride.objects.filter(
            organisation=organisation,
            institution_id__in=campuses.values_list("pk", flat=True),
            key=key,
        ).values_list("institution_id", flat=True)
    )
    return [
        {
            "institution_id": campus.pk,
            "campus": campus.name,
            "affected": locked or campus.pk not in overridden,
            "current": effective_setting(organisation, campus, key)["label"],
        }
        for campus in campuses
    ]


@transaction.atomic
def save_shared_setting(organisation, key, value, actor=None, lock=None):
    from enterprise.models import SharedSetting
    from enterprise.settings_registry import SHARED_SETTINGS

    if key not in SHARED_SETTINGS:
        raise ValidationError("Unknown shared setting.")
    if not isinstance(value, dict):
        raise ValidationError("A shared setting must be a JSON object.")
    row, _ = SharedSetting.objects.update_or_create(
        organisation=organisation, key=key,
        defaults={"value": value, "updated_by": actor},
    )
    if lock is not None:
        row.locked = bool(lock)
        row.save(update_fields=["locked", "updated_at"])
    from core.audit import log_action

    # Only the setting row changes. Campus records and issued documents keep
    # the values they already had.
    log_action(
        actor, "group.setting_changed", instance=row,
        after={"key": key, "locked": row.locked},
    )
    return row


@transaction.atomic
def save_campus_override(institution, key, value, actor=None):
    from enterprise.models import CampusSettingOverride, OrganisationMembership, SharedSetting
    from enterprise.settings_registry import SHARED_SETTINGS

    if key not in SHARED_SETTINGS:
        raise ValidationError("Unknown shared setting.")
    membership = OrganisationMembership.objects.filter(institution=institution).first()
    if membership is None:
        raise ValidationError("This campus is not in a group.")
    locked = SharedSetting.objects.filter(
        organisation=membership.organisation, key=key, locked=True
    ).exists()
    if locked:
        raise ValidationError("This setting is locked by the group. Ask the group administrator.")
    if not isinstance(value, dict):
        raise ValidationError("A setting override must be a JSON object.")
    row, _ = CampusSettingOverride.objects.update_or_create(
        organisation=membership.organisation, institution=institution, key=key,
        defaults={"value": value, "updated_by": actor},
    )
    from core.audit import log_action

    log_action(actor, "group.setting_overridden", instance=row, after={"key": key})
    return row


# ---------- consent for student-level access ----------


@transaction.atomic
def request_group_student_access(user, organisation, institution, reason, hours=24,
                                 categories=("personal_data",), request=None):
    """Create an existing-platform consent request for a group user.

    The campus administrator decides it through the normal consent screens.
    No group role can approve its own request.
    """
    from datetime import timedelta

    from enterprise.models import GroupAccessRequestLink
    from platform_admin.models import AccessRequest
    from platform_admin.services import MAX_GRANT_HOURS

    if OrganisationMembershipCheck(organisation, institution) is False:
        raise ValidationError("This campus is not attached to the organisation.")
    reason = (reason or "").strip()
    if len(reason) < 20:
        raise ValidationError("Give a reason of at least 20 characters.")
    allowed = {"personal_data", "finance", "results"}
    chosen = [code for code in categories if code in allowed]
    if not chosen:
        raise ValidationError("Choose at least one permitted data category.")
    try:
        duration = int(hours or 24)
    except (TypeError, ValueError):
        duration = 24
    hours = max(1, min(duration, MAX_GRANT_HOURS))
    access_request = AccessRequest.objects.create(
        institution=institution, requested_by=user, categories=chosen,
        mode=AccessRequest.Mode.READ, include_export=False, reason=reason,
        reference=f"Group {organisation.slug}"[:120], duration_hours=hours,
        expires_at=timezone.now() + timedelta(days=7),
    )
    GroupAccessRequestLink.objects.create(
        access_request=access_request, organisation=organisation, requested_by=user
    )
    from core.audit import log_action

    log_action(
        user, "group.access_requested", instance=access_request, request=request,
        after={"organisation_id": organisation.pk, "categories": chosen, "hours": hours},
    )
    return access_request


def OrganisationMembershipCheck(organisation, institution):
    from enterprise.models import OrganisationMembership

    return OrganisationMembership.objects.filter(
        organisation=organisation, institution=institution
    ).exists()


# ---------- dashboard aggregates ----------


def campus_aggregates(institution, term_id=None, start=None, end=None):
    """Aggregate figures only: no names, marks, fee rows or health data."""
    from academics.models import Attendance, Student
    from examinations.models import ExamCandidate
    from finance.models import CollectionTarget, Invoice, Payment
    from platform_admin.models import Subscription

    students = Student.all_objects.filter(institution=institution)
    if start is not None:
        students = students.filter(created__date__gte=start)
    if end is not None:
        students = students.filter(created__date__lte=end)
    enrolment = students.count()

    invoices = Invoice.all_objects.filter(institution=institution)
    if term_id:
        invoices = invoices.filter(term_id=term_id)
    billed = invoices.aggregate(total=models_sum())["total"] or Decimal("0")
    paid_qs = Payment.all_objects.filter(invoice__institution=institution)
    if term_id:
        paid_qs = paid_qs.filter(invoice__term_id=term_id)
    if start is not None:
        paid_qs = paid_qs.filter(paid_on__date__gte=start)
    if end is not None:
        paid_qs = paid_qs.filter(paid_on__date__lte=end)
    collected = paid_qs.aggregate(total=models_sum())["total"] or Decimal("0")
    target = (
        CollectionTarget.all_objects.filter(institution=institution)
        .order_by("-end_date", "-pk").first()
    )
    target_amount = target.amount if target is not None else Decimal("0")

    attendance_qs = Attendance.all_objects.filter(institution=institution)
    if start is not None:
        attendance_qs = attendance_qs.filter(date__gte=start)
    if end is not None:
        attendance_qs = attendance_qs.filter(date__lte=end)
    sessions = attendance_qs.count()
    present = attendance_qs.filter(present=True).count()
    attendance_rate = round(present * 100 / sessions, 1) if sessions else 0

    exams = ExamCandidate.all_objects.filter(
        institution=institution, published=True, mark__isnull=False
    )
    if term_id:
        exams = exams.filter(paper__exam__term_id=term_id)
    candidates = exams.count()
    passed = exams.filter(mark__gte=50).count()
    pass_rate = round(passed * 100 / candidates, 1) if candidates else 0

    from accounts.models import User

    staff = User.objects.filter(institution=institution, is_active=True).exclude(
        role__in=("student", "parent")
    ).count()
    subscription = Subscription.objects.filter(institution=institution).order_by(
        "-created", "-pk"
    ).first()
    subscription_status = subscription.status if subscription else "none"
    overdue = subscription is not None and subscription.status in (
        Subscription.Status.PAST_DUE, Subscription.Status.SUSPENDED
    )
    return {
        "institution_id": institution.pk,
        "campus": institution.name,
        "campus_label": getattr(institution, "group_membership", None).campus_label
        if hasattr(institution, "group_membership") else institution.name,
        "school_type": institution.type,
        "enrolment": enrolment,
        "billed": billed,
        "collected": collected,
        "target": target_amount,
        "collection_pct": round(collected * 100 / target_amount, 1) if target_amount else 0,
        "attendance": attendance_rate,
        "pass_rate": pass_rate,
        "staff": staff,
        "subscription": subscription_status,
        "overdue_subscription": overdue,
    }


def models_sum():
    """A Decimal sum expression, imported lazily to keep this module light."""
    from django.db.models import Sum

    return Sum("amount")


def group_dashboard(organisation, campus_ids=None, term_id=None, school_type=None,
                    start=None, end=None):
    """The campus comparison table and the totals behind its charts."""
    campuses = organisation.campuses()
    if campus_ids:
        campuses = campuses.filter(pk__in=campus_ids)
    if school_type:
        campuses = campuses.filter(type=school_type)
    rows = [campus_aggregates(campus, term_id, start, end) for campus in campuses]
    totals = {
        "enrolment": sum(row["enrolment"] for row in rows),
        "collected": sum((row["collected"] for row in rows), Decimal("0")),
        "target": sum((row["target"] for row in rows), Decimal("0")),
        "staff": sum(row["staff"] for row in rows),
        "attendance": round(sum(row["attendance"] for row in rows) / len(rows), 1)
        if rows else 0,
        "pass_rate": round(sum(row["pass_rate"] for row in rows) / len(rows), 1)
        if rows else 0,
    }
    return {"rows": rows, "totals": totals, "campuses": organisation.campuses()}


def refresh_group_alerts(organisation, dashboard=None):
    """Recalculate read-only alerts from the same aggregates as the dashboard."""
    from enterprise.models import GroupAlert

    dashboard = dashboard or group_dashboard(organisation)
    current = []
    with use_institution(organisation.campuses().first()) if organisation.campuses().exists() else _nullcontext():
        for row in dashboard["rows"]:
            institution_id = row["institution_id"]
            found = []
            if row["target"] and row["collected"] < row["target"] * Decimal("0.8"):
                found.append((GroupAlert.Kind.FEES,
                              f"{row['campus']} is behind on fee collection."))
            if row["attendance"] and row["attendance"] < 80:
                found.append((GroupAlert.Kind.ATTENDANCE,
                              f"{row['campus']} attendance is below 80%."))
            if row["overdue_subscription"]:
                found.append((GroupAlert.Kind.SUBSCRIPTION,
                              f"{row['campus']} has an overdue subscription."))
            for kind, message in found:
                alert, _ = GroupAlert.objects.update_or_create(
                    organisation=organisation, institution_id=institution_id, kind=kind,
                    defaults={"message": message},
                )
                current.append(alert.pk)
        # Only alerts that no longer apply are removed. An open alert keeps its
        # original created time, so a dashboard refresh is not a destructive
        # rewrite of the group's alert history.
        GroupAlert.objects.filter(organisation=organisation).exclude(pk__in=current).delete()
    return GroupAlert.objects.filter(pk__in=current).order_by("-created", "-pk")


class _nullcontext:
    def __enter__(self):
        return None

    def __exit__(self, *args):
        return False


# ---------- group billing ----------


@transaction.atomic
def create_consolidated_invoice(organisation, actor=None, period=""):
    """One existing PlatformInvoice with a line per campus and a volume discount."""
    from enterprise.models import GroupBilling, GroupInvoice, GroupInvoiceLine
    from platform_admin.models import PlatformInvoice, Subscription

    billing, _ = GroupBilling.objects.get_or_create(
        organisation=organisation, defaults={"mode": GroupBilling.Mode.CONSOLIDATED}
    )
    if billing.mode != GroupBilling.Mode.CONSOLIDATED:
        raise ValidationError("This organisation is billed campus by campus.")
    campuses = list(organisation.campuses())
    if not campuses:
        raise ValidationError("Attach at least one campus before invoicing.")
    subscription = Subscription.objects.filter(institution=campuses[0]).order_by(
        "-created", "-pk"
    ).first()
    plan = subscription.plan if subscription is not None else None
    if billing.discount_rate is None:
        rate = _plan_volume_discount(plan, len(campuses))
    else:
        rate = billing.discount_rate
    if not Decimal("0") <= rate <= Decimal("100"):
        raise ValidationError("The discount must be between 0 and 100 percent.")
    lines = []
    for campus in campuses:
        campus_plan = plan
        if campus_plan is None:
            sub = Subscription.objects.filter(institution=campus).order_by(
                "-created", "-pk"
            ).first()
            campus_plan = sub.plan if sub is not None else None
        price = campus_plan.monthly_price if campus_plan else Decimal("49.00")
        lines.append((campus, price))
    gross = sum((amount for _campus, amount in lines), Decimal("0"))
    # Quantify each line first so the invoice and its lines always add up.
    priced = [
        (campus, amount, (amount * rate / Decimal("100")).quantize(Decimal("0.01")))
        for campus, amount in lines
    ]
    discount = sum((line_discount for _campus, _amount, line_discount in priced), Decimal("0"))
    total = gross - discount
    platform_invoice = PlatformInvoice.objects.create(
        institution=campuses[0], subscription=subscription,
        number="GRP-" + timezone.now().strftime("%Y%m%d") + "-" +
        __import__("uuid").uuid4().hex[:8].upper(),
        description=f"Group platform invoice for {organisation.name}",
        amount=total, currency=getattr(campuses[0], "base_currency", "USD") or "USD",
        status=PlatformInvoice.Status.SENT, issued_on=timezone.localdate(),
    )
    group_invoice = GroupInvoice.objects.create(
        organisation=organisation, platform_invoice=platform_invoice, period=period,
        amount=gross, discount=discount, total=total, status=GroupInvoice.Status.ISSUED,
    )
    for campus, amount, line_discount in priced:
        label = getattr(campus, "group_membership", None).campus_label \
            if hasattr(campus, "group_membership") else campus.name
        GroupInvoiceLine.objects.create(
            invoice=group_invoice, institution=campus, campus_label=label,
            amount=amount, discount=line_discount,
        )
    from core.audit import log_action

    log_action(
        actor, "group.invoice_created", instance=group_invoice,
        after={"organisation_id": organisation.pk, "total": str(total)},
    )
    return group_invoice


def _plan_volume_discount(plan, campus_count):
    """Plan-based volume discount percentage."""
    base = {
        "starter": Decimal("0"),
        "standard": Decimal("5"),
        "college": Decimal("10"),
    }.get(getattr(plan, "code", ""), Decimal("0"))
    extra = Decimal("5") if campus_count >= 5 else Decimal("0")
    extra += Decimal("5") if campus_count >= 10 else Decimal("0")
    return base + extra


def notify_consolidated_invoice(group_invoice):
    """Send the grouped invoice notice through the central email function."""
    organisation = group_invoice.organisation
    if not organisation.contact_email:
        return None
    return send_email(
        BILLING_REMINDER, organisation.contact_email,
        f"Group invoice {group_invoice.number}",
        f"Hello {organisation.name},\n\nYour consolidated platform invoice "
        f"{group_invoice.number} totals {group_invoice.total}.\n\n"
        f"Each campus's line item is listed in the group portal.\n",
        institution=group_invoice.platform_invoice.institution if group_invoice.platform_invoice else None,
        related_object=group_invoice,
    )


# ---------- sponsor portal ----------


def _sponsor_school(account):
    return account.sponsor.institution


def create_sponsor_account(*, user, sponsor, role, organisation=None, sharing_level=1,
                           consent_evidence="", notify_email=False, notify_in_app=True,
                           actor=None, request=None):
    """Invite a named user to one existing finance sponsor."""
    from core.audit import log_action
    from core.email_types import STAFF_INVITATION
    from enterprise.access import SponsorRole, log_sponsor_action
    from enterprise.models import SponsorAccount

    level = int(sharing_level)
    if level not in (1, 2, 3):
        raise ValidationError("Choose a sharing level from 1 to 3.")
    if role not in (SponsorRole.ADMIN, SponsorRole.FINANCE, SponsorRole.VIEWER):
        raise ValidationError("Choose a recognised sponsor role.")
    if not sponsor.is_active:
        raise ValidationError("This sponsor is inactive.")
    if SponsorAccount.objects.filter(user=user, sponsor=sponsor).exists():
        raise ValidationError("That user already has an account for this sponsor.")
    if level > 1 and len((consent_evidence or "").strip()) < 20:
        raise ValidationError("Give a clear reason for sharing above totals only.")
    account = SponsorAccount.objects.create(
        user=user, sponsor=sponsor, organisation=organisation, role=role,
        sharing_level=level, notify_email=bool(notify_email),
        notify_in_app=bool(notify_in_app),
        consent_evidence=(consent_evidence or "").strip(),
        consented_at=timezone.now() if level > 1 else None,
    )
    if request is None:
        log_action(
            actor, "sponsor.account_created", instance=account,
            after={"role": role, "sharing_level": level},
            institution=sponsor.institution,
        )
    else:
        log_sponsor_action(
            request, "sponsor.account_created", account,
            {"sharing_level": level, "sponsor_id": sponsor.pk},
        )
    recipient = user.email or sponsor.email
    if recipient:
        send_email(
            STAFF_INVITATION, recipient,
            "Your sponsor portal invitation",
            "Hello,\n\nA sponsor portal account has been created for you. "
            "Sign in at /sponsor/ and enter this verification code:\n\n"
            f"{account.verification_token}\n\n"
            "If you did not expect this invitation, contact the school.\n",
            institution=sponsor.institution, related_object=account,
        )
    return account


def verify_sponsor_account(user, token, request=None):
    """Verify an invited account with the one-time code from its invitation."""
    from enterprise.access import log_sponsor_action
    from enterprise.models import SponsorAccount

    token = (token or "").strip()
    account = SponsorAccount.objects.filter(
        user=user, verification_token=token, is_active=True, verified_at__isnull=True
    ).first()
    if account is None:
        raise ValidationError("That verification code does not match an invited account.")
    account.verified_at = timezone.now()
    account.save(update_fields=["verified_at", "updated_at"])
    if request is not None:
        log_sponsor_action(request, "sponsor.verified", account, {"kind": "verification"})
    return account


def resend_sponsor_verification(account, request=None):
    """Send the same invitation code again through the central email pipeline."""
    from core.email_types import STAFF_INVITATION
    from enterprise.access import log_sponsor_action

    recipient = account.user.email or account.sponsor.email
    if recipient:
        send_email(
            STAFF_INVITATION, recipient, "Your sponsor portal verification code",
            f"Your verification code is {account.verification_token}.\n",
            institution=_sponsor_school(account), related_object=account,
        )
    if request is not None:
        log_sponsor_action(request, "sponsor.verification_resent", account,
                           {"kind": "verification"})
    return recipient


def create_sponsor_link(account, student, percentage=None, fixed_amount=None,
                        valid_from=None, valid_to=None, reference="", request=None):
    """Create an ordinary finance sponsorship for one student."""
    from core.audit import log_action
    from finance.models import Sponsorship

    school = _sponsor_school(account)
    if student.institution_id != school.pk:
        raise ValidationError("That student does not belong to this sponsor's school.")
    if not (percentage or fixed_amount):
        raise ValidationError("Enter a percentage or a fixed amount.")
    with use_institution(school):
        link = Sponsorship.objects.create(
            sponsor=account.sponsor, student=student, percentage=percentage,
            fixed_amount=fixed_amount, valid_from=valid_from, valid_to=valid_to,
            reference=reference, is_active=True,
        )
    if request is None:
        log_action(
            request.user if request else None, "sponsor.student_linked", instance=link,
            after={"sponsor_id": account.sponsor.pk}, institution=school,
        )
    else:
        from enterprise.access import log_sponsor_action

        log_sponsor_action(request, "sponsor.student_linked", account,
                           {"sponsor_id": account.sponsor.pk})
    return link


def set_sharing_level(account, level, evidence="", request=None):
    """Change what the sponsor agrees to share, with 2FA for named detail."""
    from enterprise.access import log_sponsor_action, require_sponsor_sensitive_action

    level = int(level)
    if level == 0:
        return withdraw_sponsor_sharing(account, request=request)
    if level not in (1, 2, 3):
        raise ValidationError("Choose a sharing level from 1 to 3.")
    if level > account.sharing_level:
        if len((evidence or "").strip()) < 20:
            raise ValidationError("Give a clear reason for sharing more information.")
        if level == 3:
            require_sponsor_sensitive_action(account, request)
    account.sharing_level = level
    account.consent_evidence = (evidence or "").strip() if level > 1 else ""
    account.consented_at = timezone.now()
    account.withdrawn_at = None
    account.save(update_fields=[
        "sharing_level", "consent_evidence", "consented_at", "withdrawn_at", "updated_at"
    ])
    if request is not None:
        log_sponsor_action(request, "sponsor.sharing_changed", account,
                           {"sharing_level": level})
    notify_sponsor(
        account, "Sponsor sharing changed",
        f"Your sharing level is now {account.get_sharing_level_display()}.",
        "/sponsor/settings/", "access",
    )
    return account


def withdraw_sponsor_sharing(account, reason="", request=None):
    """Stop all student-level sharing now, including existing consent grants."""
    from enterprise.access import log_sponsor_action
    from platform_admin.models import AccessRequest, DataAccessGrant

    now = timezone.now()
    grants = DataAccessGrant.objects.filter(
        platform_admin=account.user, institution=_sponsor_school(account),
        revoked_at__isnull=True, expired_at__isnull=True,
    )
    request_ids = list(grants.values_list("access_request_id", flat=True))
    grants.update(
        revoked_at=now, revoked_by=account.user,
        revoked_reason=(reason or "Sponsor withdrew sharing consent.")[:200],
    )
    if request_ids:
        AccessRequest.objects.filter(pk__in=request_ids).update(
            status=AccessRequest.Status.REVOKED,
            decision_reason=(reason or "Sponsor withdrew sharing consent.")[:200],
        )
    account.sharing_level = 0
    account.withdrawn_at = now
    account.consent_evidence = ""
    account.save(update_fields=[
        "sharing_level", "withdrawn_at", "consent_evidence", "updated_at"
    ])
    if request is not None:
        log_sponsor_action(request, "sponsor.sharing_withdrawn", account,
                           {"sharing_level": 0, "kind": "withdrawal"})
    notify_sponsor(
        account, "Sponsor sharing withdrawn",
        "Named and coded student information is no longer available. "
        "Any student-data grant has been revoked.",
        "/sponsor/settings/", "access",
    )
    return account


def notify_sponsor(account, title, body, link="", category="general", email_type=None):
    """Opt-in sponsor notification. Nothing is sent unless the user agreed."""
    from core.email_types import GENERAL
    from core.notifications import notify, send_email

    sent = []
    if account.notify_in_app:
        sent.append(notify(
            account.user, title, body, link=link, category=category,
            institution=_sponsor_school(account),
        ))
    if account.notify_email:
        recipient = account.user.email or account.sponsor.email
        if recipient:
            sent.append(send_email(
                email_type or GENERAL, recipient, title, body,
                institution=_sponsor_school(account), related_object=account,
            ))
    return sent


def sponsor_student_label(account, student, request=None):
    """The most student detail this sponsor may see right now.

    Level 3 needs both the sponsor's consent and a live institution grant. If
    the grant is missing or withdrawn, the label drops back to a code; it never
    falls back to a name.
    """
    from enterprise.access import student_level_grant

    if not account.is_verified or account.has_withdrawn:
        return "Student"
    if account.sharing_level >= 3:
        grant = student_level_grant(
            account.user, _sponsor_school(account),
            ("personal_data", "finance"), request=request,
        )
        if grant is not None:
            return student.name
    if account.sharing_level >= 2:
        return f"Student {student.pk}"
    return "Student"


def _sponsored_invoice_rows(account, start=None, end=None):
    """Invoice-level coverage from active sponsorships, without student names."""
    from finance.models import Invoice, Sponsorship

    school = _sponsor_school(account)
    with use_institution(school):
        links = Sponsorship.all_objects.filter(sponsor=account.sponsor, is_active=True)
        invoices = Invoice.all_objects.filter(
            institution=school, student__sponsorships__sponsor=account.sponsor
        ).distinct()
        if start:
            invoices = invoices.filter(due_date__gte=start)
        if end:
            invoices = invoices.filter(due_date__lte=end)
        rows = []
        for invoice in invoices.select_related("student__user").order_by("due_date", "pk"):
            caps = [
                link.covers(invoice) for link in links if link.student_id == invoice.student_id
            ]
            cap = sum(caps, Decimal("0"))
            balance = max(invoice.balance, Decimal("0"))
            rows.append({
                "invoice_id": invoice.pk,
                "description": invoice.description,
                "due_date": invoice.due_date,
                "student": invoice.student,
                "amount": invoice.amount,
                "balance": balance,
                "cap": cap,
                "coverage": min(cap, balance),
            })
        return rows


def sponsor_commitments(account, start=None, end=None, request=None):
    """What the sponsor has committed, with a sharing-safe student label."""
    rows = []
    for row in _sponsored_invoice_rows(account, start, end):
        rows.append({
            **row,
            "label": sponsor_student_label(account, row["student"], request=request),
        })
    return {
        "rows": rows,
        "totals": {
            "amount": sum((r["amount"] for r in rows), Decimal("0")),
            "cap": sum((r["cap"] for r in rows), Decimal("0")),
            "coverage": sum((r["coverage"] for r in rows), Decimal("0")),
            "outstanding": sum((r["balance"] for r in rows), Decimal("0")),
        },
    }


def sponsor_statement(account, start=None, end=None, request=None):
    """Discounts, payments and credit notes recorded for this sponsor."""
    from finance.models import Discount, Payment, Waiver

    school = _sponsor_school(account)
    marker = f"SPONSOR-{account.sponsor_id}-"
    rows = []
    with use_institution(school):
        # Every row is scoped to the sponsor's school. ``all_objects`` reaches
        # every institution, so an identically named payer elsewhere must never
        # appear on this sponsor's statement.
        sources = [
            Discount.all_objects.filter(institution=school, sponsor=account.sponsor),
            Payment.all_objects.filter(
                institution=school,
                reference__icontains=marker,
            ) | Payment.all_objects.filter(
                institution=school,
                payer_name__iexact=account.sponsor.name,
            ),
            Waiver.all_objects.filter(
                institution=school,
                reason__icontains=marker,
            ) | Waiver.all_objects.filter(
                institution=school,
                reason__icontains=account.sponsor.name,
            ),
        ]
        for queryset in sources:
            date_field = "paid_on__date" if queryset.model is Payment else "created__date"
            if start:
                queryset = queryset.filter(**{date_field + "__gte": start})
            if end:
                queryset = queryset.filter(**{date_field + "__lte": end})
            for row in queryset.select_related("invoice__student__user").distinct():
                student = getattr(getattr(row, "invoice", None), "student", None)
                rows.append({
                    "date": (
                        row.paid_on.date() if queryset.model is Payment else row.created.date()
                    ),
                    "kind": {
                        Discount: "Scholarship",
                        Payment: "Payment",
                        Waiver: "Credit note",
                    }[queryset.model],
                    "reference": getattr(row, "receipt_no", "") or getattr(row, "note_no", "")
                    or getattr(row, "name", "") or str(row.pk),
                    "label": sponsor_student_label(
                        account, student, request=request
                    ) if student is not None else "Institution",
                    "amount": row.amount,
                    "payment_id": row.pk if queryset.model is Payment else None,
                })
    rows.sort(key=lambda item: (item["date"], str(item["reference"])))
    return {
        "rows": rows,
        "totals": {
            "paid": sum((r["amount"] for r in rows if r["kind"] == "Payment"), Decimal("0")),
            "scholarship": sum(
                (r["amount"] for r in rows if r["kind"] == "Scholarship"), Decimal("0")
            ),
            "credit_note": sum(
                (r["amount"] for r in rows if r["kind"] == "Credit note"), Decimal("0")
            ),
        },
    }


def sponsor_receipts(account, start=None, end=None, request=None):
    """Only the payments already selected for this sponsor's statement."""
    rows = sponsor_statement(account, start, end, request)["rows"]
    return [row for row in rows if row["kind"] == "Payment" and row["payment_id"]]


def allocate_sponsor_payment(account, invoice, amount=None,
                             method=None, request=None):
    """Pay a sponsored invoice, capped by the sponsorship and invoice balance."""
    from enterprise.access import log_sponsor_action
    from finance.models import Payment, Sponsorship
    from finance.services import receive_payment

    if method not in Payment.Method.values:
        raise ValidationError("Choose a recognised payment method.")
    school = _sponsor_school(account)
    from finance.models import Invoice as InvoiceModel

    with use_institution(school):
        target = InvoiceModel.all_objects.select_for_update().filter(
            pk=getattr(invoice, "pk", invoice), institution=school
        ).first()
        if target is None:
            raise ValidationError("That invoice is not at this school.")
        links = Sponsorship.all_objects.filter(
            sponsor=account.sponsor, student=target.student, is_active=True
        )
        cap = sum((link.covers(target) for link in links), Decimal("0"))
        balance = max(target.balance, Decimal("0"))
        cap = min(cap, balance)
        value = Decimal(amount) if amount not in (None, "") else cap
        if value <= 0:
            raise ValidationError("There is no sponsored balance to pay.")
        if value > cap:
            raise ValidationError(f"This sponsorship is capped at {cap:.2f}.")
        payment, credit = receive_payment(
            student=target.student, invoice=target, amount=value, method=method,
            reference=f"SPONSOR-{account.sponsor_id}-{target.pk}",
            payer_name=account.sponsor.name, received_by=account.user,
        )
    if request is not None:
        log_sponsor_action(
            request, "sponsor.payment", account,
            {"invoice_id": target.pk, "amount": str(value), "method": method},
        )
    notify_sponsor(
        account, "Sponsor payment recorded",
        f"{value:.2f} was applied to invoice {target.number or target.pk}.",
        "/sponsor/statement/", "finance",
    )
    return payment, credit


def issue_sponsor_credit_note(account, invoice, amount, reason, request=None):
    """Issue an ordinary immutable finance credit note through its save hooks."""
    from enterprise.access import log_sponsor_action
    from finance.models import Invoice as InvoiceModel
    from finance.models import Sponsorship, Waiver

    reason = (reason or "").strip()
    if len(reason) < 10:
        raise ValidationError("Give a reason of at least 10 characters.")
    school = _sponsor_school(account)
    with use_institution(school):
        target = InvoiceModel.all_objects.filter(
            pk=getattr(invoice, "pk", invoice), institution=school
        ).first()
        if target is None:
            raise ValidationError("That invoice is not at this school.")
        links = Sponsorship.all_objects.filter(
            sponsor=account.sponsor, student=target.student, is_active=True
        )
        cap = min(
            sum((link.covers(target) for link in links), Decimal("0")),
            max(target.balance, Decimal("0")),
        )
        value = Decimal(amount)
        if value <= 0 or value > cap:
            raise ValidationError(f"The credit note must be between 0 and {cap:.2f}.")
        waiver = Waiver(
            invoice=target, amount=value,
            reason=f"SPONSOR-{account.sponsor_id}-{target.pk}: {reason}"[:200],
            recorded_by=account.user,
        )
        waiver.save()
    if request is not None:
        log_sponsor_action(
            request, "sponsor.credit_note", account,
            {"invoice_id": target.pk, "amount": str(value), "kind": "credit_note"},
        )
    notify_sponsor(
        account, "Sponsor credit note issued",
        f"A credit note for {value:.2f} was posted to the school ledger.",
        "/sponsor/statement/", "finance",
    )
    return waiver


def donor_report(institution=None, start=None, end=None):
    """Scholarship and donor totals by sponsor. Never contains student names."""
    from finance.models import (
        Discount,
        Payment,
        Sponsor,
        Sponsorship,
        Waiver,
    )

    if institution is None:
        from core.models import Institution
        from core.tenancy import current_institution_id

        institution = Institution.objects.filter(pk=current_institution_id()).first()
    if institution is None:
        return {"rows": [], "totals": {}}
    rows = []
    with use_institution(institution):
        for sponsor in Sponsor.all_objects.filter(institution=institution, is_active=True):
            marker = f"SPONSOR-{sponsor.pk}-"
            committed = sum(
                _sponsored_invoice_rows_for_sponsor(sponsor, start, end), Decimal("0")
            )
            sources = [
                Discount.all_objects.filter(institution=institution, sponsor=sponsor),
                Payment.all_objects.filter(
                    institution=institution, reference__icontains=marker,
                ) | Payment.all_objects.filter(
                    institution=institution, payer_name__iexact=sponsor.name,
                ),
                Waiver.all_objects.filter(
                    institution=institution, reason__icontains=marker,
                ) | Waiver.all_objects.filter(
                    institution=institution, reason__icontains=sponsor.name,
                ),
            ]
            filtered = []
            for queryset in sources:
                date_field = "paid_on__date" if queryset.model is Payment else "created__date"
                if start:
                    queryset = queryset.filter(**{date_field + "__gte": start})
                if end:
                    queryset = queryset.filter(**{date_field + "__lte": end})
                filtered.append(queryset)
            discounts, payments, credits = filtered
            rows.append({
                "sponsor_id": sponsor.pk,
                "sponsor": sponsor.name,
                "students": Sponsorship.all_objects.filter(
                    institution=institution, sponsor=sponsor, is_active=True
                ).count(),
                "committed": committed,
                "scholarship": sum(
                    (r.amount for r in discounts), Decimal("0")
                ),
                "paid": sum((r.amount for r in payments), Decimal("0")),
                "credit_note": sum((r.amount for r in credits), Decimal("0")),
            })
    return {
        "rows": rows,
        "totals": {
            "students": sum((r["students"] for r in rows), 0),
            "committed": sum((r["committed"] for r in rows), Decimal("0")),
            "scholarship": sum((r["scholarship"] for r in rows), Decimal("0")),
            "paid": sum((r["paid"] for r in rows), Decimal("0")),
            "credit_note": sum((r["credit_note"] for r in rows), Decimal("0")),
        },
    }


def _sponsored_invoice_rows_for_sponsor(sponsor, start=None, end=None):
    from finance.models import Invoice, Sponsorship

    links = Sponsorship.all_objects.filter(sponsor=sponsor, is_active=True)
    invoices = Invoice.all_objects.filter(
        institution=sponsor.institution, student__sponsorships__sponsor=sponsor
    ).distinct()
    if start:
        invoices = invoices.filter(due_date__gte=start)
    if end:
        invoices = invoices.filter(due_date__lte=end)
    return [
        sum((link.covers(invoice) for link in links
             if link.student_id == invoice.student_id), Decimal("0"))
        for invoice in invoices
    ]
