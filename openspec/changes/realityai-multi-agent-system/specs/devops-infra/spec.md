## ADDED Requirements

### Requirement: Docker Compose one-click startup
The system SHALL provide a docker-compose.yml that starts all services with a single command: FastAPI gateway, Spring Boot core, PostgreSQL, ChromaDB, Redis, and React dashboard. Health checks and dependency ordering SHALL be configured.

#### Scenario: Full stack starts with docker-compose up
- **WHEN** a developer runs `docker-compose up`
- **THEN** all services start in the correct dependency order and become healthy

#### Scenario: Environment configuration via .env
- **WHEN** a developer configures .env.example with their settings
- **THEN** all services read their configuration from environment variables

### Requirement: GitHub Actions CI/CD pipeline
The system SHALL provide a GitHub Actions workflow that runs: lint (ruff for Python, eslint for TypeScript), unit tests, integration tests, and Docker build verification.

#### Scenario: CI pipeline runs on pull request
- **WHEN** a pull request is opened against main
- **THEN** the CI pipeline runs lint, unit tests, and integration tests

#### Scenario: Build verification
- **WHEN** the CI pipeline reaches the build step
- **THEN** Docker images for all services build successfully

### Requirement: Branch protection for main
The main branch SHALL require CI pipeline to pass before merging. Direct pushes to main SHALL be blocked.

#### Scenario: Failed CI blocks merge
- **WHEN** the CI pipeline fails on a PR targeting main
- **THEN** the PR cannot be merged until CI passes

### Requirement: Comprehensive README
The project SHALL include a README.md with: architecture diagram (Mermaid or image), quick start guide, configuration reference, demo GIF, metrics summary table, and license.

#### Scenario: New developer onboarding
- **WHEN** a new developer clones the repository
- **THEN** they can follow the README to get the system running with `docker-compose up`
