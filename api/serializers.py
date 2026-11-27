from django.contrib.auth.models import User
from rest_framework import serializers

from training.models import TrainingSet, TrainingSetItem


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "is_superuser", "is_staff")
        read_only_fields = fields


class TrainingSetSerializer(serializers.ModelSerializer):
    owner = serializers.CharField(source="owner.username", read_only=True)
    total = serializers.IntegerField(read_only=True)
    pending = serializers.IntegerField(read_only=True)
    rework = serializers.IntegerField(read_only=True)
    passed = serializers.IntegerField(read_only=True)
    pass_rate = serializers.SerializerMethodField()

    class Meta:
        model = TrainingSet
        fields = (
            "id",
            "name",
            "owner",
            "created_at",
            "total",
            "pending",
            "rework",
            "passed",
            "pass_rate",
        )
        read_only_fields = fields

    def get_pass_rate(self, obj) -> int | None:
        decided = obj.passed + obj.rework
        return round(obj.passed / decided * 100) if decided else None


class TrainingSetItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrainingSetItem
        fields = (
            "id",
            "label",
            "fen",
            "san_best",
            "status",
            "attempts",
            "order",
            "last_trained_at",
            "puzzle_id",
            "turning_point_id",
        )
        read_only_fields = fields


class ReworkItemSerializer(serializers.ModelSerializer):
    set_id = serializers.IntegerField(source="training_set_id", read_only=True)
    set_name = serializers.CharField(source="training_set.name", read_only=True)
    book_title = serializers.SerializerMethodField()
    book_slug = serializers.SerializerMethodField()

    class Meta:
        model = TrainingSetItem
        fields = (
            "id",
            "label",
            "status",
            "attempts",
            "san_best",
            "last_trained_at",
            "set_id",
            "set_name",
            "book_title",
            "book_slug",
            "puzzle_id",
            "turning_point_id",
        )
        read_only_fields = fields

    def get_book_title(self, obj) -> str | None:
        return obj.puzzle.book.title if obj.puzzle is not None else None

    def get_book_slug(self, obj) -> str | None:
        return obj.puzzle.book.slug if obj.puzzle is not None else None
