from django.urls import path

from . import views

urlpatterns = [
    path("auth/csrf/", views.csrf_token, name="csrf"),
    path("auth/login/", views.login_view, name="login"),
    path("auth/logout/", views.logout_view, name="logout"),
    path("auth/me/", views.me_view, name="me"),
    path("train-sets/", views.TrainingSetList.as_view(), name="training_set_list"),
    path(
        "train-sets/<int:pk>/",
        views.TrainingSetDetail.as_view(),
        name="training_set_detail",
    ),
    path(
        "train-sets/<int:pk>/items/",
        views.TrainingSetItems.as_view(),
        name="training_set_items",
    ),
]
