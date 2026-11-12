# Run all tests
test:
    uv run pytest

# Lint code
lint:
    uv run ruff check

# Format code
format:
    uv run ruff format

# Type check code
type:
    uv run ty check

# Check code quality
check: lint format type

# Check everything
verify: check test
