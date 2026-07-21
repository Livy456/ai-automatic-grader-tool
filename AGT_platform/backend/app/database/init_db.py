from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker #, declarative_base

#Base = declarative_base()
engine = None
SessionLocal = sessionmaker(autocommit=False, autoflush=False)


def init_db(database_url: str):
    """
    Initialize the database engine and session.

    Parameters:
        database_url: The URL for the database.
    
    """

    global engine, SessionLocal
    print("database url (inside init_db.py): ", database_url)
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)

    print("updated database url (inside init_db.py): ", database_url)
    
    engine_kwargs = {"pool_pre_ping": True}
    if not database_url.startswith("sqlite:"):
        engine_kwargs.update(
            {
                "pool_size": 5,
                "max_overflow": 10,
                "pool_recycle": 1800,
            }
        )
    engine = create_engine(database_url, **engine_kwargs)
    SessionLocal.configure(bind=engine)

    return engine