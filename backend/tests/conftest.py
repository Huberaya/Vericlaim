import os

# Use local isolated SQLite database for unit tests to ensure sub-second execution
os.environ["DATABASE_URL"] = "sqlite:///./vericlaim_test.db"
