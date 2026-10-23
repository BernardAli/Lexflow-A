from .models import Membership
from django.conf import settings
from django.http import JsonResponse


class MaintenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if settings.LEXFLOW_MAINTENANCE:
            response = JsonResponse({"error": "maintenance", "detail": "The office is temporarily offline for maintenance."}, status=503)
            response["Retry-After"] = "60"
            return response
        return self.get_response(request)


class TenantMiddleware:
    """Resolves the active tenant from the signed-in user's memberships.

    Views never trust a tenant id from the URL or form: every query is scoped
    with request.tenant, which is resolved here from the session + membership.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.tenant = None
        request.membership = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            memberships = Membership.objects.select_related("tenant").filter(user=user)
            if settings.LEXFLOW_DEPLOYMENT == "dedicated":
                memberships = memberships.filter(tenant__slug=settings.LEXFLOW_TENANT_SLUG)
            wanted = request.session.get("tenant_id")
            chosen = None
            for m in memberships:
                if wanted and m.tenant_id == wanted:
                    chosen = m
                    break
            if chosen is None:
                chosen = memberships.first()
            if chosen is not None:
                request.tenant = chosen.tenant
                request.membership = chosen
                request.session["tenant_id"] = chosen.tenant_id
        return self.get_response(request)
