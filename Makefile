.PHONY: check format lint typecheck test clean coverage coverage-html install

# Default target
all: check

install:
	@echo "🔧 Installing dependencies..."
	uv sync --all-extras

# Format code
format:
	@echo "🎨 Formatting code..."
	uv run black .
	uv run isort .

# Run linter
lint:
	@echo "🔍 Running linter..."
	uv run ruff check .

# Run type checker
typecheck:
	@echo "📝 Running type checker..."
	uv run mypy src

# Run tests
test:
	@echo "🧪 Running tests..."
	uv run pytest -v

# Run tests with coverage
coverage:
	@echo "📊 Running tests with coverage..."
	uv run pytest -v --cov=src --cov-report=term-missing

# Generate HTML coverage report
coverage-html:
	@echo "📊 Generating HTML coverage report..."
	uv run pytest -v --cov=src --cov-report=html
	@echo "✨ Coverage report generated in htmlcov/index.html"

# Run all checks
check: format lint typecheck test
	@echo "✨ All checks passed!"

# Clean up cache files
clean:
	@echo "🧹 Cleaning up..."
	find . -type d -name "__pycache__" -exec rm -r {} +
	find . -type d -name ".pytest_cache" -exec rm -r {} +
	find . -type d -name ".mypy_cache" -exec rm -r {} +
	find . -type d -name ".ruff_cache" -exec rm -r {} +
	find . -type d -name "htmlcov" -exec rm -r {} +
	find . -type f -name "*.pyc" -delete
	find . -type f -name ".coverage" -delete
	@echo "✨ Cleanup complete!" 