from django.urls import path

from . import views

app_name = "intake"
urlpatterns = [
    path("inbox/", views.inbox, name="inbox"),
    path("inbox/new/", views.new_intake, name="new"),
    path("inbox/<str:ref>/process/", views.process, name="process"),
    path("documents/<int:pk>/", views.download, name="download"),
    path("plugin/outlook/<str:ref>/", views.taskpane, name="taskpane"),
]
