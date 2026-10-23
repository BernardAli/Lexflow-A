import csv
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.conf import settings
from django.http import Http404, HttpResponse
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.contrib.auth import views as auth_views
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache

from apps.audit.services import record, verify_chain
from apps.matters.models import Matter

from . import analytics, metrics
from .decorators import ADMINS, tenant_view
from .models import Membership, Tenant


class LoginView(auth_views.LoginView):
    template_name = "auth/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        m = Membership.objects.filter(user=form.get_user()).select_related("tenant").first()
        if m:
            record(m.tenant, "auth.login", "Signed in", actor=form.get_user(), object_type="user",
                   object_ref=form.get_user().get_username())
        return response


@require_POST
@tenant_view()
def switch_tenant(request):
    m = Membership.objects.filter(user=request.user, tenant_id=request.POST.get("tenant")).first()
    if not m:
        raise PermissionDenied
    request.session["tenant_id"] = m.tenant_id
    messages.success(request, f"Switched to {m.tenant.name}.")
    return redirect("core:dashboard")


@never_cache
@tenant_view()
def dashboard(request):
    t = request.tenant
    waiting = (
        Matter.objects.filter(tenant=t, status__in=[Matter.Status.NEEDS_REVIEW, Matter.Status.UNCLASSIFIED,
                                                    Matter.Status.READY, Matter.Status.APPROVED])
        .select_related("matter_type", "message").annotate(
            conflict_count=Count("fields", filter=Q(fields__status="conflict")),
            open_count=Count("fields", filter=Q(fields__status__in=["review", "missing"])),
        ).order_by("created_at")[:8]
    )
    chain = verify_chain(t)
    analytics_form = analytics.AnalyticsFilterForm(request.GET, tenant=t)
    report = analytics.build_report(t, analytics_form.scope(), now=analytics_form.now, chain=chain) if analytics_form.is_valid() else None
    case_page = Paginator(report["work_items"], 25).get_page(request.GET.get("case_page")) if report else None
    return render(request, "core/dashboard.html", {
        "pipeline": metrics.pipeline_counts(t),
        "waiting": waiting,
        "analytics_form": analytics_form, "analytics": report, "case_page": case_page,
        "chain": chain,
        "nav": "overview",
    })


@never_cache
@tenant_view()
def analytics_export(request):
    form = analytics.AnalyticsFilterForm(request.GET, tenant=request.tenant)
    if not form.is_valid():
        return HttpResponse("Invalid analytics filters. Check the dates, workflow, source and cohort.", status=400, content_type="text/plain")
    scope = form.scope()
    report = analytics.build_report(request.tenant, scope, now=form.now)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="lexflow-analytics-{scope.start}-{scope.end}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    for row in analytics.csv_rows(report):
        writer.writerow([analytics.safe_csv_cell(value) for value in row])
    return response


@tenant_view()
def security(request):
    t = request.tenant
    token = request.session.pop("new_api_token", None)
    if request.method == "POST":
        if not request.membership.can_configure:
            raise PermissionDenied
        before = {"profile": t.security_profile, "external_ai": t.allow_external_ai, "retention": t.retention_days}
        profile = request.POST.get("security_profile")
        if settings.LEXFLOW_DEPLOYMENT == "dedicated" and profile not in (None, "private"):
            messages.error(request, "This pilot uses its configured dedicated deployment.")
            return redirect("core:security")
        if profile in Tenant.SecurityProfile.values:
            t.security_profile = profile
        t.allow_external_ai = request.POST.get("allow_external_ai") == "on"
        try:
            t.retention_days = max(30, min(3650, int(request.POST.get("retention_days", t.retention_days))))
            t.baseline_minutes_per_matter = Decimal(request.POST.get("baseline_minutes_per_matter"))
            t.baseline_rekey_error_pct = Decimal(request.POST.get("baseline_rekey_error_pct"))
            t.baseline_audit_completeness_pct = Decimal(request.POST.get("baseline_audit_completeness_pct"))
        except (TypeError, ValueError, InvalidOperation):
            messages.error(request, "Baseline and retention values must be numbers.")
            return redirect("core:security")
        baseline = [t.baseline_minutes_per_matter, t.baseline_rekey_error_pct, t.baseline_audit_completeness_pct]
        if any(not v.is_finite() or v < 0 for v in baseline) or any(v > 100 for v in baseline[1:]):
            messages.error(request, "Baseline values must be finite, positive, and percentages at most 100.")
            return redirect("core:security")
        from django.utils import timezone
        t.baseline_recorded_at = timezone.now()
        t.save()
        after = {"profile": t.security_profile, "external_ai": t.allow_external_ai, "retention": t.retention_days}
        record(t, "security.settings_changed", "Changed security settings", actor=request.user, obj=t,
               object_ref=t.slug, details={"before": before, "after": after})
        messages.success(request, "Security settings saved.")
        return redirect("core:security")
    return render(request, "core/security.html", {
        "deployment": settings.LEXFLOW_DEPLOYMENT,
        "profiles": [
            ("eu_saas", "EU secure SaaS", "Fastest rollout. Processing in an approved EU region on a dedicated tenant with managed updates.",
             ["EU region", "Dedicated tenant", "Managed updates"]),
            ("hybrid", "Hybrid secure agent", "A local agent handles office-side intake; encrypted service performs approved processing.",
             ["Local agent", "Reduced exposure", "Controlled egress"]),
            ("private", "Private / dedicated", "Dedicated infrastructure or a private environment for maximum isolation.",
             ["Dedicated VPC", "Private AI option", "Customer-held keys"]),
        ],
        "chain": verify_chain(t),
        "new_token": token,
        "members": Membership.objects.filter(tenant=t).select_related("user"),
        "nav": "security",
    })


@require_POST
@tenant_view(roles=ADMINS)
def regenerate_token(request):
    token = request.tenant.issue_api_token()
    request.session["new_api_token"] = token
    record(request.tenant, "security.api_token_issued", "Issued a new plugin API token", actor=request.user,
           obj=request.tenant, object_ref=request.tenant.slug, details={"prefix": token[:10]})
    return redirect("core:security")


def error_403(request, exception=None):
    return render(request, "errors/403.html", {"message": str(exception or "")}, status=403)


def error_404(request, exception=None):
    return render(request, "errors/404.html", status=404)


@tenant_view()
def presentation(request):
    if not settings.LEXFLOW_DEMO_MODE:
        raise Http404
    from apps.intake.models import IntakeMessage
    messages = IntakeMessage.objects.filter(tenant=request.tenant, is_demo=True).select_related("matter")
    return render(request, "core/presentation.html", {
        "flagship": messages.filter(external_id="demo-schmidt-purchase").first(),
        "new_case": messages.filter(external_id="demo-becker-freising").first(),
        "scan_case": messages.filter(external_id="demo-scanned-purchase").first(),
        "ocr_enabled": settings.LEXFLOW_OCR_ENABLED,
        "chain": verify_chain(request.tenant), "nav": "presentation",
    })
