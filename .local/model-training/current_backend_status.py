"""Read-only status check; never trains, publishes runs or changes configuration."""
import json

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from firewatch.config import get_settings
from firewatch.db.models import Detection, Region, Run, Score, WeatherRecord, ModelVersion
from firewatch.db.session import Database
from firewatch.main import create_app

settings = get_settings()
database = Database(settings)
with database.session() as session:
    session.execute(text('SET TRANSACTION READ ONLY'))
    print(json.dumps({
        'active_scorer': settings.active_scorer,
        'detections': session.scalar(select(func.count()).select_from(Detection)),
        'runs': session.scalar(select(func.count()).select_from(Run)),
        'scores': session.scalar(select(func.count()).select_from(Score)),
        'weather_records': session.scalar(select(func.count()).select_from(WeatherRecord)),
        'registered_models': session.scalar(select(func.count()).select_from(ModelVersion)),
        'regions': [{'name': r.name, 'enabled': r.enabled} for r in session.scalars(select(Region))],
    }, indent=2))
database.close()
with TestClient(create_app(settings)) as client:
    for path in ('/health/ready', '/api/v1/models/current', '/api/v1/runs'):
        response = client.get(path)
        print(path, response.status_code)
