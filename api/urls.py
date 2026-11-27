from django.urls import path

from . import views

urlpatterns = [
    path("auth/csrf/", views.csrf_token, name="csrf"),
    path("auth/login/", views.login_view, name="login"),
    path("auth/logout/", views.logout_view, name="logout"),
    path("auth/me/", views.me_view, name="me"),
    path("train-sets/", views.TrainingSetList.as_view(), name="training_set_list"),
    path(
        "train-sets/options/",
        views.training_options,
        name="training_options",
    ),
    path(
        "train-sets/clone/",
        views.clone_training_set,
        name="training_set_clone",
    ),
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
    path(
        "train-sets/<int:pk>/mark-all/",
        views.mark_all_view,
        name="training_set_mark_all",
    ),
    path(
        "train-sets/<int:pk>/items/<int:item_pk>/",
        views.training_item_detail,
        name="training_item_detail",
    ),
    path(
        "train-sets/<int:pk>/items/<int:item_pk>/mark/",
        views.training_item_mark,
        name="training_item_mark",
    ),
    path(
        "train-sets/<int:pk>/items/<int:item_pk>/action/",
        views.training_item_action,
        name="training_item_action",
    ),
    path("rework/", views.ReworkList.as_view(), name="rework_list"),
]
