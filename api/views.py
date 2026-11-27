import json

from django.contrib.auth import authenticate
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.db.models import Count, Q
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_protect, ensure_csrf_cookie
from django.views.decorators.http import require_POST
from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from analysis.models import TurningPoint
from books.models import Book
from chessdata.models import Player
from training.models import TrainingSetItem
from training.services import (
    add_entries,
    apply_item_action,
    clone_set,
    create_set,
    mark_all,
    mark_item,
    next_item,
    set_for,
    sets_for,
)

from .serializers import (
    ReworkItemSerializer,
    TrainingSetItemSerializer,
    TrainingSetSerializer,
    UserSerializer,
)

CATEGORIES = tuple(TrainingSetItem.Status.values)
EDIT_ACTIONS = {"rotate", "toggle_turn", "set_fen"}


def _annotate_sets(queryset):
    return queryset.annotate(
        total=Count("items"),
        pending=Count(
            "items", filter=Q(items__status=TrainingSetItem.Status.PENDING)
        ),
        passed=Count(
            "items", filter=Q(items__status=TrainingSetItem.Status.PASSED)
        ),
        rework=Count(
            "items", filter=Q(items__status=TrainingSetItem.Status.REWORK)
        ),
    ).order_by("-created_at")


def _annotated_set(user, pk):
    return get_object_or_404(_annotate_sets(sets_for(user)), pk=pk)


def _stats(training_set):
    counts = training_set.items.aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(status=TrainingSetItem.Status.PENDING)),
        passed=Count("id", filter=Q(status=TrainingSetItem.Status.PASSED)),
        rework=Count("id", filter=Q(status=TrainingSetItem.Status.REWORK)),
    )
    decided = counts["passed"] + counts["rework"]
    counts["pass_rate"] = (
        round(counts["passed"] / decided * 100) if decided else None
    )
    return counts


def _parse_entries(raw) -> list[dict]:
    """Validate the JSON source entries used by create/add-positions."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("Add at least one source.")
    entries = []
    for data in raw:
        if not isinstance(data, dict):
            raise ValueError("Invalid source.")
        source_type = data.get("source_type")
        if source_type == "book":
            book_slug = str(data.get("book_slug") or "").strip()
            if not book_slug:
                continue
            entry = {"source_type": "book", "book_slug": book_slug}
            for key in ("pages_from", "pages_to", "numbers_from", "numbers_to"):
                value = data.get(key)
                if value not in (None, ""):
                    entry[key] = value
            numbers = data.get("numbers")
            if numbers:
                cleaned = [str(value).strip() for value in numbers]
                entry["numbers"] = [value for value in cleaned if value]
            entries.append(entry)
        elif source_type == "turning_points":
            player = str(data.get("player") or "").strip().lower()
            if not player:
                continue
            entry = {"source_type": "turning_points", "player": player}
            for key in ("date_from", "date_to"):
                value = data.get(key)
                if value not in (None, ""):
                    entry[key] = value
            classifications = data.get("classifications")
            if classifications:
                entry["classifications"] = [
                    str(value) for value in classifications if value
                ]
            entries.append(entry)
        elif source_type == "rework":
            from_set = data.get("from_set")
            if not from_set:
                continue
            entry = {"source_type": "rework", "from_set": from_set}
            entries.append(entry)
        else:
            raise ValueError(f"Unknown source type: {source_type!r}.")
    if not entries:
        raise ValueError("Add at least one source.")
    return entries


@ensure_csrf_cookie
@api_view(["GET"])
@permission_classes([AllowAny])
@authentication_classes([])
def csrf_token(request):
    """Issue a CSRF cookie for the React app."""
    return Response({"csrfToken": get_token(request)})


@csrf_protect
@require_POST
def login_view(request):
    """Plain Django JSON view so CSRF is enforced by Django middleware.

    DRF's APIView is csrf_exempt and only SessionAuthentication enforces CSRF
    for already-authenticated requests, which is too late for login.
    """
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "Invalid JSON."}, status=400)
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    user = authenticate(request, username=username, password=password)
    if user is None:
        return JsonResponse(
            {"detail": "Invalid username or password."}, status=400
        )
    auth_login(request, user)
    return JsonResponse(UserSerializer(user).data)


@api_view(["POST"])
def logout_view(request):
    auth_logout(request)
    return Response({"ok": True})


@api_view(["GET"])
def me_view(request):
    return Response(UserSerializer(request.user).data)


class TrainingSetList(ListAPIView):
    serializer_class = TrainingSetSerializer

    def get_queryset(self):
        return _annotate_sets(sets_for(self.request.user))

    def post(self, request):
        name = str(request.data.get("name") or "").strip()
        if not name:
            return Response(
                {"detail": "Give the set a name."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            entries = _parse_entries(request.data.get("entries"))
            training_set = create_set(name, entries, request.user)
        except ValueError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            TrainingSetSerializer(_annotated_set(request.user, training_set.pk)).data,
            status=status.HTTP_201_CREATED,
        )


class TrainingSetDetail(RetrieveAPIView):
    serializer_class = TrainingSetSerializer

    def get_queryset(self):
        return _annotate_sets(sets_for(self.request.user))


@api_view(["POST"])
def clone_training_set(request):
    source = set_for(request.user, request.data.get("source_id"))
    if source is None:
        return Response(
            {"detail": "Unknown set to clone."}, status=status.HTTP_404_NOT_FOUND
        )
    name = str(request.data.get("name") or "").strip() or f"Copy of {source.name}"
    training_set = clone_set(name, source, request.user)
    return Response(
        TrainingSetSerializer(_annotated_set(request.user, training_set.pk)).data,
        status=status.HTTP_201_CREATED,
    )


class TrainingSetItems(ListAPIView):
    serializer_class = TrainingSetItemSerializer

    def get_queryset(self):
        training_set = get_object_or_404(
            sets_for(self.request.user), pk=self.kwargs["pk"]
        )
        items = training_set.items.all()
        status_filter = self.request.query_params.get("status", "")
        if status_filter in CATEGORIES:
            items = items.filter(status=status_filter)
        return items.order_by("order", "id")

    def post(self, request, pk):
        training_set = get_object_or_404(sets_for(request.user), pk=pk)
        try:
            entries = _parse_entries(request.data.get("entries"))
            added = add_entries(training_set, entries, request.user)
        except ValueError as exc:
            return Response(
                {"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response({"added": added}, status=status.HTTP_201_CREATED)


@api_view(["POST"])
def mark_all_view(request, pk):
    training_set = get_object_or_404(sets_for(request.user), pk=pk)
    result = request.data.get("result")
    category = request.data.get("status", TrainingSetItem.Status.PENDING)
    if result not in (
        TrainingSetItem.Status.PASSED,
        TrainingSetItem.Status.REWORK,
    ):
        return Response(
            {"detail": "Bad result."}, status=status.HTTP_400_BAD_REQUEST
        )
    if category not in CATEGORIES:
        category = TrainingSetItem.Status.PENDING
    count = mark_all(training_set, result, status=category)
    return Response({"marked": count, "stats": _stats(training_set)})


@api_view(["GET"])
def training_item_detail(request, pk, item_pk):
    training_set = get_object_or_404(sets_for(request.user), pk=pk)
    item = get_object_or_404(
        TrainingSetItem.objects.select_related("puzzle"),
        pk=item_pk,
        training_set=training_set,
    )
    category = request.query_params.get("status", TrainingSetItem.Status.PENDING)
    if category not in CATEGORIES:
        category = TrainingSetItem.Status.PENDING
    nxt = next_item(training_set, category)
    return Response(
        {
            "item": TrainingSetItemSerializer(item).data,
            "set": TrainingSetSerializer(
                _annotated_set(request.user, training_set.pk)
            ).data,
            "category": category,
            "next_item_id": nxt.pk if nxt else None,
        }
    )


@api_view(["POST"])
def training_item_mark(request, pk, item_pk):
    training_set = get_object_or_404(sets_for(request.user), pk=pk)
    item = get_object_or_404(
        TrainingSetItem, pk=item_pk, training_set=training_set
    )
    result = request.data.get("result")
    if result not in (
        TrainingSetItem.Status.PASSED,
        TrainingSetItem.Status.REWORK,
    ):
        return Response(
            {"detail": "Bad result."}, status=status.HTTP_400_BAD_REQUEST
        )
    category = request.data.get("status", TrainingSetItem.Status.PENDING)
    if category not in CATEGORIES:
        category = TrainingSetItem.Status.PENDING
    mark_item(item, result)
    nxt = next_item(training_set, category)
    return Response(
        {
            "ok": True,
            "item_status": item.status,
            "attempts": item.attempts,
            "next_item_id": nxt.pk if nxt else None,
            "stats": _stats(training_set),
        }
    )


@api_view(["POST"])
def training_item_action(request, pk, item_pk):
    if not request.user.is_superuser:
        return Response(
            {"detail": "Only an administrator can change positions."},
            status=status.HTTP_403_FORBIDDEN,
        )
    training_set = get_object_or_404(sets_for(request.user), pk=pk)
    item = get_object_or_404(
        TrainingSetItem, pk=item_pk, training_set=training_set
    )
    action = request.data.get("action")
    if action not in EDIT_ACTIONS:
        return Response(
            {"detail": "Bad action."}, status=status.HTTP_400_BAD_REQUEST
        )
    try:
        apply_item_action(item, action, request.data.get("fen"))
    except ValueError:
        return Response(
            {"detail": "Invalid FEN."}, status=status.HTTP_400_BAD_REQUEST
        )
    return Response({"ok": True, "fen": item.fen})


class ReworkList(ListAPIView):
    serializer_class = ReworkItemSerializer

    def get_queryset(self):
        items = TrainingSetItem.objects.filter(
            status=TrainingSetItem.Status.REWORK,
            training_set__in=sets_for(self.request.user),
        ).select_related("training_set", "puzzle__book")
        set_pk = self.request.query_params.get("set") or ""
        if set_pk.isdigit():
            selected_set = set_for(self.request.user, set_pk)
            if selected_set is not None:
                items = items.filter(training_set=selected_set)
        book_slug = (self.request.query_params.get("book") or "").strip()
        if book_slug:
            selected_book = Book.objects.filter(slug=book_slug).first()
            if selected_book is not None:
                items = items.filter(puzzle__book=selected_book)
        return items.order_by("training_set__name", "order", "id")


@api_view(["GET"])
def training_options(request):
    sets = _annotate_sets(sets_for(request.user)).order_by("name")
    rework_sets = [
        training_set for training_set in sets if training_set.rework > 0
    ]
    rework_books = (
        Book.objects.filter(
            puzzles__training_items__status=TrainingSetItem.Status.REWORK,
            puzzles__training_items__training_set__in=sets_for(
                request.user
            ),
        )
        .distinct()
        .order_by("title")
    )
    return Response(
        {
            "books": [
                {"slug": book.slug, "title": book.title}
                for book in Book.objects.order_by("title")
            ],
            "players": list(
                Player.objects.order_by("username").values_list(
                    "username", flat=True
                )
            ),
            "classifications": [
                {"value": value, "label": label}
                for value, label in TurningPoint.Classification.choices
            ],
            "sets": [
                {
                    "id": training_set.pk,
                    "name": training_set.name,
                    "rework": training_set.rework,
                }
                for training_set in sets
            ],
            "rework_sets": [
                {
                    "id": training_set.pk,
                    "name": training_set.name,
                    "rework": training_set.rework,
                }
                for training_set in rework_sets
            ],
            "rework_books": [
                {"slug": book.slug, "title": book.title}
                for book in rework_books
            ],
        }
    )
