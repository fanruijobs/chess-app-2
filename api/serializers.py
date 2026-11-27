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
            "status",
            "attempts",
            "order",
            "puzzle_id",
            "turning_point_id",
        )
        read_only_fields = fields
