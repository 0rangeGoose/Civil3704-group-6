# Transport Informatics AR Project - Universal Build System
# This Makefile provides a common interface regardless of implementation language

.PHONY: help build run test clean setup install docker-build docker-run

# Default target
.DEFAULT_GOAL := help

# Detect if docker-compose is available
DOCKER_COMPOSE := $(shell command -v docker-compose 2> /dev/null || command -v docker 2> /dev/null && echo "docker compose")

help: ## Show this help message
	@echo "Transport Informatics AR Project - Available Commands:"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Quick start: make setup && make build && make run"

setup: ## Initial setup - copy configuration templates
	@echo "Setting up project configuration..."
	@if [ ! -f config.json ]; then \
		cp config.example.json config.json; \
		echo "✓ Created config.json from template"; \
	else \
		echo "✓ config.json already exists"; \
	fi
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
		echo "✓ Created .env from template"; \
	else \
		echo "✓ .env already exists"; \
	fi
	@echo "⚠ Please edit config.json and .env with your GTFS feed URLs and API keys"

install: ## Install dependencies (language-specific)
	@echo "Installing dependencies..."
	@if [ -f requirements.txt ]; then \
		echo "→ Installing Python dependencies..."; \
		pip install -r requirements.txt; \
	elif [ -f package.json ]; then \
		echo "→ Installing Node.js dependencies..."; \
		npm install; \
	elif [ -f Gemfile ]; then \
		echo "→ Installing Ruby dependencies..."; \
		bundle install; \
	elif [ -f go.mod ]; then \
		echo "→ Installing Go dependencies..."; \
		go mod download; \
	else \
		echo "ℹ No dependency file found. Add language-specific install commands here."; \
	fi

build: install ## Build the project
	@echo "Building project..."
	@if [ -f requirements.txt ]; then \
		echo "✓ Python project - dependencies installed"; \
	elif [ -f package.json ]; then \
		echo "→ Building Node.js project..."; \
		npm run build 2>/dev/null || echo "No build script defined in package.json"; \
	elif [ -f go.mod ]; then \
		echo "→ Building Go project..."; \
		go build -o bin/ar-transit ./src; \
	else \
		echo "ℹ Add your build commands in the Makefile"; \
	fi
	@echo "✓ Build complete"

run: ## Run the AR installation
	@echo "Starting AR installation..."
	@if [ -f src/main.py ]; then \
		python src/main.py; \
	elif [ -f src/index.js ]; then \
		node src/index.js; \
	elif [ -f src/main.js ]; then \
		node src/main.js; \
	elif [ -f bin/ar-transit ]; then \
		./bin/ar-transit; \
	else \
		echo "❌ No main entry point found. Please specify in Makefile or run manually."; \
		exit 1; \
	fi

test: ## Run tests
	@echo "Running tests..."
	@if [ -f pytest.ini ] || [ -d tests/ ]; then \
		pytest tests/ -v; \
	elif [ -f package.json ]; then \
		npm test; \
	elif [ -f go.mod ]; then \
		go test ./...; \
	else \
		echo "ℹ No tests configured yet. Add test commands in the Makefile"; \
	fi

clean: ## Clean build artifacts and cache
	@echo "Cleaning project..."
	@rm -rf __pycache__ .pytest_cache .coverage htmlcov
	@rm -rf node_modules/.cache dist build
	@rm -rf bin/
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "✓ Clean complete"

docker-build: ## Build Docker container
	@echo "Building Docker container..."
	@$(DOCKER_COMPOSE) build

docker-run: ## Run project in Docker container
	@echo "Running in Docker..."
	@$(DOCKER_COMPOSE) up

docker-stop: ## Stop Docker containers
	@$(DOCKER_COMPOSE) down

docker-clean: ## Clean Docker containers and images
	@$(DOCKER_COMPOSE) down -v --rmi all

dev: ## Run in development mode with auto-reload
	@echo "Starting development mode..."
	@if [ -f src/main.py ]; then \
		python -m watchdog.auto_restart src/main.py; \
	elif [ -f package.json ]; then \
		npm run dev || npm run watch || echo "No dev script in package.json"; \
	else \
		echo "Add your dev mode command here"; \
	fi

logs: ## View application logs
	@if [ -d logs/ ]; then \
		tail -f logs/*.log; \
	else \
		echo "No logs directory found"; \
	fi

.PHONY: validate
validate: ## Validate configuration files
	@echo "Validating configuration..."
	@python -c "import json; json.load(open('config.json'))" 2>/dev/null && echo "✓ config.json is valid" || echo "❌ config.json has errors"
	@if [ -f .env ]; then \
		echo "✓ .env exists"; \
	else \
		echo "⚠ .env not found - run 'make setup'"; \
	fi
