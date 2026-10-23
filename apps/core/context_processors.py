def lexflow(request):
    from django.conf import settings
    ctx = {"tenant": getattr(request, "tenant", None), "membership": getattr(request, "membership", None)}
    ctx["demo_mode"] = settings.LEXFLOW_DEMO_MODE
    tenant = ctx["tenant"]
    if tenant is not None:
        from apps.matters.models import Matter

        ctx["nav_review_count"] = Matter.objects.filter(
            tenant=tenant, status__in=[Matter.Status.NEEDS_REVIEW, Matter.Status.UNCLASSIFIED]
        ).count()
        ctx["nav_memberships"] = request.user.memberships.select_related("tenant")
    return ctx
