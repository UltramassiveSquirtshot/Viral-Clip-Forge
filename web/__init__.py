from flask import Flask
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates")
    app.config["PROJECT_ROOT"] = PROJECT_ROOT
    app.config["SECRET_KEY"] = "vcf-local-only"

    from .routes import bp
    app.register_blueprint(bp)

    return app
