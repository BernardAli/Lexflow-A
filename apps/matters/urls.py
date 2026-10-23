from django.urls import path

from . import views

app_name = "matters"
urlpatterns = [
    path("<str:ref>/reopen/", views.reopen, name="reopen"),
    path("<str:ref>/sources/<int:pk>/acknowledge/", views.acknowledge_source, name="acknowledge_source"),
    path("", views.matter_list, name="list"),
    path("<str:ref>/", views.review, name="review"),
    path("<str:ref>/fields/<int:field_id>/", views.field_action, name="field_action"),
    path("<str:ref>/teach/", views.teach, name="teach"),
    path("<str:ref>/approve/", views.approve, name="approve"),
    path("<str:ref>/type/", views.set_type, name="set_type"),
    path("<str:ref>/reprocess/", views.reprocess, name="reprocess"),
]
