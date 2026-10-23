from django.urls import path

from . import views

app_name = "connectors"
urlpatterns = [
    path("transfers/<str:ref>/deliver/", views.deliver, name="deliver"),
    path("transfers/<str:ref>/demo-receive/", views.demo_receive, name="demo_receive"),
    path("transfers/<str:ref>/acknowledge/", views.acknowledge, name="acknowledge"),
    path("", views.index, name="index"),
    path("send/<str:ref>/", views.send, name="send"),
    path("preview/<str:ref>/<int:pk>/", views.preview, name="preview"),
    path("transfers/<str:ref>/download/", views.download, name="download"),
]
