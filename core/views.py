"""
Views — thin, no business logic (HackSoft Django Styleguide).

Responsibility: validate input, call service, serialize output.
"""

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.services import route_plan


class RoutePlanApi(APIView):
    """POST or GET /api/route/ — plan a fuel-optimized route."""

    class InputSerializer(serializers.Serializer):
        start = serializers.CharField(required=True)
        end = serializers.CharField(required=True)

    class FuelStopOutputSerializer(serializers.Serializer):
        mileage = serializers.FloatField()
        lat = serializers.FloatField()
        lon = serializers.FloatField()
        name = serializers.CharField()
        address = serializers.CharField()
        price = serializers.FloatField()
        gallons = serializers.FloatField()
        cost = serializers.FloatField()

    class OutputSerializer(serializers.Serializer):
        route_geojson = serializers.DictField()
        stops = serializers.ListField(child=serializers.DictField())
        total_fuel_cost = serializers.FloatField()
        total_gallons = serializers.FloatField()
        total_miles = serializers.FloatField()
        mpg_used = serializers.IntegerField()

    def get(self, request):
        return self._handle(request)

    def post(self, request):
        return self._handle(request)

    def _handle(self, request):
        input_ser = self.InputSerializer(
            data=request.query_params if request.method == "GET" else request.data
        )
        input_ser.is_valid(raise_exception=True)

        try:
            result = route_plan(**input_ser.validated_data)
        except ValueError as exc:
            return Response(
                {"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST
            )

        output_ser = self.OutputSerializer(result)
        return Response(output_ser.data)
