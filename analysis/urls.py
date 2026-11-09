from django.urls import path

from analysis import views

app_name = "analysis"

urlpatterns = [
    path("", views.players_list, name="players_list"),
    path("analysis/", views.analysis_board, name="analysis_board"),
    path("players/fetch/", views.players_fetch, name="players_fetch"),
    path("players/<str:username>/", views.players_detail, name="players_detail"),
    path("games/<str:uuid>/", views.game_detail, name="game_detail"),
    path("games/<str:uuid>/pdf/", views.game_pdf, name="game_pdf"),
    path(
        "games/<str:uuid>/turning-points/<int:tp_id>/llm/",
        views.tp_llm,
        name="tp_llm",
    ),
]
