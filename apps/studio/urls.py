from django.urls import path

from . import views

app_name = "studio"
urlpatterns = [
    path("", views.index, name="index"),
    path("<slug:key>/", views.matter_type, name="matter_type"),
    path("<slug:key>/toggle/", views.toggle_type, name="toggle_type"),
    path("<slug:key>/fields/<int:pk>/", views.save_field, name="save_field"),
    path("<slug:key>/fields/<int:pk>/labels/", views.anchors, name="anchors"),
    path("<slug:key>/documents/<int:pk>/", views.save_doctype, name="save_doctype"),
]
