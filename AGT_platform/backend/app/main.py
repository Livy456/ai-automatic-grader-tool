from flask import Flask
from flask_cors import CORS
from .config import Config
# from extensions import init_db, Base, engine

# from app import extensions
# from app.extensions import Base
from app.extensions import init_db
from app.models import Base

from .auth import bp as auth_bp, init_oauth
from .tasks import init_celery
from .routes.health import bp as health_bp
# from .routes.assignments import bp as assignments_bp # OLD VERSION OF THE ASSIGNMENTS
from .routes.submissions import bp as submissions_bp
from .routes.admin import bp as admin_bp
from .routes_assignments import bp as assignments_bp
import os

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config_obj = Config()  # for celery task access
    
    # Configure session for OAuth state parameter (CSRF protection)


    # BEGIN => MIGHT HAVE TO REMOVE THIS LATER!!
    # Authlib uses Flask sessions to store OAuth state
    app.config['SECRET_KEY'] = os.getenv("SECRET_KEY")    
    app.config['SESSION_COOKIE_SECURE'] = True  # Use secure cookies in production (HTTPS)
    app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection
    # END => MIGHT HAVE TO REMOVE THIS LATER!!
    
    CORS(app, supports_credentials=True)

    # init_db(app.config["DATABASE_URL"])
    # Base.metadata.create_all(bind=engine)
    
    # extensions.init_db(app.config["DATABASE_URL"])
    # Base.metadata.create_all(bind=extensions.engine)

    print("DATABASE_URL =", app.config.get("DATABASE_URL"))
    print("DATABASE_URL_ACTUAL: ", app.config["DATABASE_URL"])
    engine = init_db(app.config["DATABASE_URL"])
    #Base.metadata.create_all(bind=engine) # affects alembic migration, will remove later!!

    init_oauth(app)
    init_celery(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(assignments_bp)
    app.register_blueprint(submissions_bp)
    app.register_blueprint(admin_bp)
    return app

if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=5000, debug=True)
