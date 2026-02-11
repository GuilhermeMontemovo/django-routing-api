from django.urls import path

from core.views import RoutePlanApi

app_name = "core"

urlpatterns = [
    path("route/", RoutePlanApi.as_view(), name="route-plan"),
]
