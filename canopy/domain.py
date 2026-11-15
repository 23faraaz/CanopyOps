from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, Field, field_validator

STATIONS = {
    'north-ridge': {'name': 'North Ridge', 'location': 'Highland forest', 'lat': 56.82, 'lon': -4.12},
    'cedar-valley': {'name': 'Cedar Valley', 'location': 'Valley woodland', 'lat': 54.46, 'lon': -3.04},
    'oak-meadow': {'name': 'Oak Meadow', 'location': 'Lowland reserve', 'lat': 52.48, 'lon': -1.89},
    'coastal-grove': {'name': 'Coastal Grove', 'location': 'Coastal woodland', 'lat': 50.72, 'lon': -3.53},
}

class Reading(BaseModel):
    event_id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,80}$')
    station_id: Literal['north-ridge', 'cedar-valley', 'oak-meadow', 'coastal-grove']
    observed_at: datetime
    temperature: float = Field(ge=-50, le=70, allow_inf_nan=False)
    humidity: float = Field(ge=0, le=100, allow_inf_nan=False)
    soil_moisture: float = Field(ge=0, le=100, allow_inf_nan=False)

    @field_validator('observed_at')
    @classmethod
    def aware_timestamp(cls, value):
        if value.tzinfo is None:
            raise ValueError('observed_at must include a timezone')
        if value.timestamp() > datetime.now(timezone.utc).timestamp() + 300:
            raise ValueError('observed_at is too far in the future')
        return value.astimezone(timezone.utc)

def status(reading):
    reasons = []
    if reading['temperature'] >= 35:
        reasons.append('High temperature')
    if reading['soil_moisture'] < 25:
        reasons.append('Low soil moisture')
    return reasons

def advance(previous, reading):
    data = reading.model_dump(mode='json')
    if previous and previous['reading']['observed_at'] >= data['observed_at']:
        return previous
    reasons = status(data)
    incidents = list(previous.get('incidents', [])) if previous else []
    active = next((i for i in incidents if i['resolved_at'] is None), None)
    if reasons and active is None:
        incidents.insert(0, {'id': data['event_id'], 'station_id': data['station_id'],
                            'opened_at': data['observed_at'], 'resolved_at': None, 'reasons': reasons})
    elif reasons and active:
        active['reasons'] = reasons
    elif active:
        active['resolved_at'] = data['observed_at']
    return {'reading': data, 'reasons': reasons, 'incidents': incidents[:100]}
