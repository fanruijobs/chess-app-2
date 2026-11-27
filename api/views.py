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
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from training.models import TrainingSetItem
from training.services import sets_for

from .serializers import (
    TrainingSetItemSerializer,
    TrainingSetSerializer,
    UserSerializer,
)


def _sets_for(user):
    return sets_for(user).annotate(
        total=Count("items"),
        pending=Count("items", filter=Q(items__status=TrainingSetItem.Status.PENDING)),
        passed=Count("items", filter=Q(items__status=TrainingSetItem.Status.PASSED)),
        rework=Count("items", filter=Q(items__status=TrainingSetItem.Status.REWORK)),
    ).order_by("-created_at")


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
        return _sets_for(self.request.user)


class TrainingSetDetail(RetrieveAPIView):
    serializer_class = TrainingSetSerializer

    def get_queryset(self):
        return _sets_for(self.request.user)


class TrainingSetItems(ListAPIView):
    serializer_class = TrainingSetItemSerializer

    def get_queryset(self):
        training_set = get_object_or_404(
            sets_for(self.request.user), pk=self.kwargs["pk"]
        )
        items = training_set.items.all()
        status_filter = self.request.query_params.get("status", "")
        if status_filter in TrainingSetItem.Status.values:
            items = items.filter(status=status_filter)
        return items.order_by("order", "id")
