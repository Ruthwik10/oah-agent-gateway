"""OAH Agent Gateway package configuration."""
from pathlib import Path

from dotenv import load_dotenv

# Load local development/demo configuration before source or provider modules read
# environment variables. Explicit process environment variables retain precedence.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
