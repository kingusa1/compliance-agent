"""Migrate all data from SQLite to Postgres.

Usage:
    DATABASE_URL=postgresql://user:pass@host/db python scripts/migrate_sqlite_to_postgres.py

Requires:
    - DATABASE_URL env var set to the Postgres connection string
    - The SQLite database file at ./compliance.db (or SQLITE_PATH env var)
    - Postgres tables already created (run `alembic upgrade head` first)
"""

import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add the backend directory to sys.path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def migrate():
    postgres_url = os.environ.get("DATABASE_URL")
    sqlite_path = os.environ.get("SQLITE_PATH", "./compliance.db")

    if not postgres_url:
        print("ERROR: DATABASE_URL environment variable is required.")
        print("Set it to your Postgres connection string, e.g.:")
        print("  DATABASE_URL=postgresql://postgres:password@db.xxx.supabase.co:5432/postgres")
        sys.exit(1)

    if not postgres_url.startswith("postgresql"):
        print(f"ERROR: DATABASE_URL must be a Postgres URL, got: {postgres_url[:30]}...")
        sys.exit(1)

    if not os.path.exists(sqlite_path):
        print(f"ERROR: SQLite database not found at {sqlite_path}")
        print("Set SQLITE_PATH env var to point to your SQLite database file.")
        sys.exit(1)

    # Connect to both databases
    sqlite_engine = create_engine(
        f"sqlite:///{sqlite_path}",
        connect_args={"check_same_thread": False},
    )
    postgres_engine = create_engine(postgres_url)

    SqliteSession = sessionmaker(bind=sqlite_engine)
    PostgresSession = sessionmaker(bind=postgres_engine)

    sqlite_db = SqliteSession()
    postgres_db = PostgresSession()

    # Tables to migrate in order (respecting foreign key dependencies)
    tables = ["scripts", "script_versions", "calls", "call_checkpoints"]

    try:
        for table_name in tables:
            print(f"\nMigrating table: {table_name}")

            # Read all rows from SQLite
            rows = sqlite_db.execute(text(f"SELECT * FROM {table_name}")).fetchall()
            columns = sqlite_db.execute(text(f"SELECT * FROM {table_name} LIMIT 0")).keys()
            col_list = list(columns)

            if not rows:
                print(f"  No rows found in {table_name}, skipping.")
                continue

            print(f"  Found {len(rows)} rows")

            # Insert into Postgres
            col_str = ", ".join(col_list)
            param_str = ", ".join(f":{c}" for c in col_list)
            insert_sql = text(f"INSERT INTO {table_name} ({col_str}) VALUES ({param_str})")

            batch_size = 500
            for i in range(0, len(rows), batch_size):
                batch = rows[i : i + batch_size]
                row_dicts = [dict(zip(col_list, row)) for row in batch]
                postgres_db.execute(insert_sql, row_dicts)
                postgres_db.commit()
                print(f"  Inserted batch {i // batch_size + 1} ({len(batch)} rows)")

            # Verify row counts match
            pg_count = postgres_db.execute(
                text(f"SELECT COUNT(*) FROM {table_name}")
            ).scalar()
            sqlite_count = len(rows)

            if pg_count == sqlite_count:
                print(f"  Verified: {pg_count} rows in Postgres == {sqlite_count} rows in SQLite")
            else:
                print(
                    f"  WARNING: Row count mismatch! "
                    f"Postgres={pg_count}, SQLite={sqlite_count}"
                )

        print("\nMigration complete!")

    except Exception as e:
        postgres_db.rollback()
        print(f"\nERROR during migration: {e}")
        sys.exit(1)
    finally:
        sqlite_db.close()
        postgres_db.close()


if __name__ == "__main__":
    migrate()
