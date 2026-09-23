import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase

load_dotenv()

_database_url = os.getenv("DATABASE_URL")
if not _database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy BE/.env.example to BE/.env and fill in the Neon connection string."
    )

# Neon gives a plain postgresql:// URL; point SQLAlchemy at psycopg 3.
DATABASE_URL = make_url(_database_url).set(drivername="postgresql+psycopg")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


class Base(DeclarativeBase):
    pass
