from django.urls import path

from books import views

app_name = "books"

urlpatterns = [
    path("books/", views.index, name="index"),
    path("books/<slug:slug>/", views.book_detail, name="book_detail"),
    path(
        "books/<slug:slug>/pages/<int:page_number>/pdf/",
        views.book_page_pdf,
        name="book_page_pdf",
    ),
    path("review/", views.review_queue, name="review_queue"),
    path("review/<int:pk>/", views.review_puzzle, name="review_puzzle"),
    path("bad-fens/", views.bad_fens, name="bad_fens"),
    path("pages/<int:page_id>/diagram/<int:index>.png", views.diagram_crop, name="diagram_crop"),
    path("puzzles/<int:pk>/board.svg", views.puzzle_board_svg, name="puzzle_board_svg"),
    path("puzzles/<int:pk>/", views.puzzle_view, name="puzzle_view"),
]
