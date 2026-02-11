from django.contrib.gis.db import models
from django.utils import timezone


class BaseModel(models.Model):
    """Abstract base with audit timestamps (HackSoft Styleguide pattern)."""

    created_at = models.DateTimeField(db_index=True, default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class FuelStation(BaseModel):
    opis_id = models.IntegerField(unique=True, help_text="Original ID from CSV")
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=20)
    retail_price = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        db_index=True,
    )
    location = models.PointField(srid=4326, spatial_index=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Fuel Station"
        verbose_name_plural = "Fuel Stations"

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state}) - ${self.retail_price}"
