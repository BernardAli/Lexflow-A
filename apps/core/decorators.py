from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def tenant_view(roles=None):
    """Require login + an active tenant membership, optionally a specific role."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if request.tenant is None or request.membership is None:
                raise PermissionDenied("You are not a member of any notary office.")
            if roles and request.membership.role not in roles:
                raise PermissionDenied("Your role does not allow this action.")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


APPROVERS = ("notary", "admin")
ADMINS = ("admin",)
