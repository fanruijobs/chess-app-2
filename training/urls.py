from django.urls import path

from training import views

app_name = "training"

urlpatterns = [
    path("train-sets/", views.set_list, name="set_list"),
    path("rework/", views.rework_list, name="rework_list"),
    path("train-sets/<int:pk>/", views.set_detail, name="set_detail"),
    path("train-sets/<int:pk>/pdf/", views.set_pdf, name="set_pdf"),
    path(
        "train-sets/<int:pk>/items/<int:item_pk>/",
        views.item_session, name="item_session",
    ),
    path(
        "train-sets/<int:pk>/items/<int:item_pk>/action/",
        views.item_action, name="item_action",
    ),
    path(
        "train-sets/<int:pk>/items/<int:item_pk>/mark/",
        views.mark, name="item_mark",
    ),
    path("train-sets/<int:pk>/mark-all/", views.mark_all_view, name="set_mark_all"),
]
