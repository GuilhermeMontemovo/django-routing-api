from django.contrib.gis.db import models

class FuelStation(models.Model):
    opis_id = models.IntegerField(unique=True, help_text="ID original do CSV")
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=20)
    retail_price = models.DecimalField(max_digits=10, decimal_places=3)

    # O campo espacial. SRID 4326 = WGS84 (Padrão GPS: Lat/Lon)
    location = models.PointField(srid=4326, spatial_index=True)

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) - ${self.retail_price}"