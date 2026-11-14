"""python -m backend.server.export_openapi > contracts/openapi.json

Writes the API description (OpenAPI 3.1) for generated clients. It does not start anything."""
import json

from backend.server.app import create_app
from backend.server.settings import Settings


def main() -> None:
    app = create_app(Settings(source="fixtures", cache_dir=None))
    print(json.dumps(app.openapi(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
