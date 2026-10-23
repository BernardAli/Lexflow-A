from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path, reverse_lazy

from apps.core import views as core
from apps.intake import api

urlpatterns = [
    path("", include(([
        path("", core.dashboard, name="dashboard"),
        path("login/", core.LoginView.as_view(), name="login"),
        path("logout/", auth_views.LogoutView.as_view(), name="logout"),
        path("password-reset/", auth_views.PasswordResetView.as_view(
            template_name="auth/password_reset.html",
            email_template_name="auth/password_reset_email.txt",
            subject_template_name="auth/password_reset_subject.txt",
            success_url=reverse_lazy("core:password_reset_done"),
        ), name="password_reset"),
        path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(
            template_name="auth/password_reset_done.html",
        ), name="password_reset_done"),
        path("password-reset/confirm/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
            template_name="auth/password_reset_confirm.html",
            success_url=reverse_lazy("core:password_reset_complete"),
        ), name="password_reset_confirm"),
        path("password-reset/complete/", auth_views.PasswordResetCompleteView.as_view(
            template_name="auth/password_reset_complete.html",
        ), name="password_reset_complete"),
        path("office/switch/", core.switch_tenant, name="switch_tenant"),
        path("security/", core.security, name="security"),
        path("presentation/", core.presentation, name="presentation"),
        path("security/token/", core.regenerate_token, name="regenerate_token"),
    ], "core"))),
    path("", include("apps.intake.urls")),
    path("matters/", include("apps.matters.urls")),
    path("studio/", include("apps.studio.urls")),
    path("connectors/", include("apps.connectors.urls")),
    path("audit/", include("apps.audit.urls")),
    path("api/v1/intake/", api.intake, name="api_intake"),
    path("api/v1/matters/<str:ref>/", api.matter_detail, name="api_matter"),
    path("admin/", admin.site.urls),
]

handler403 = "apps.core.views.error_403"
handler404 = "apps.core.views.error_404"
