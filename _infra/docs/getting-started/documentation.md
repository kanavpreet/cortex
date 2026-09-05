# Documentation Maintenance

This document explains how Matik maintains documentation and how to contribute to keeping our docs up-to-date.

## Overview

Matik uses a combination of manual documentation and **automated documentation maintenance** powered by Claude Code (airchat) to keep documentation synchronized with code changes. This ensures our documentation stays current as the codebase evolves.

## Documentation Structure

### Root Documentation Files

Located in the repository root:

- **`CLAUDE.md`** - Project overview, architecture, development commands, and guidance for Claude Code
  - Architecture overview and component descriptions
  - Development commands and workflows
  - Key dependencies and patterns
  - Environment variables reference

- **`README.md`** - High-level project description and quick start guide

### Portal Documentation

Located in `_infra/docs/`:

- **`getting-started/`** - Setup and onboarding guides
  - `getting-access.md` - Access and permissions
  - `local-development.md` - Local development setup
  - `pre-commit-hooks.md` - Pre-commit hooks setup and usage
  - `documentation.md` - This file

- **`architecture/`** - System architecture and design decisions
  - Infrastructure diagrams
  - Component interactions
  - Technical specifications

- **`development/`** - Development guides
  - Database setup and migrations
  - Testing strategies
  - Debugging tips

- **`decisions/`** - Architecture Decision Records (ADRs)
  - Technology choices
  - Design patterns
  - Trade-off analysis

### Navigation

Portal navigation is configured in `_infra/portal.yml` for the internal documentation portal.

## Automated Documentation Maintenance

### Claude Code Slash Commands

We use **Claude Code** (airchat) slash commands to automate documentation updates. These commands analyze code changes and update relevant documentation.

#### Available Commands

1. **`/update-local-development-docs`** - Updates CLAUDE.md, README.md, local-development.md, portal.yml
   - Monitors: `common/models/`, `common/clients/`, `common/daos/`, service main.go files, go.mod, docker-compose.yaml, .env.example
   - Updates: Architecture documentation, dependencies, configuration guides

2. **`/update-ci-cd-docs`** - Updates CI/CD documentation
   - Monitors: `_infra/ci/`, `_infra/cd/`, `_infra/deployboard.yml`
   - Updates: CI/CD setup and workflow documentation

3. **`/update-pre-commit-hooks`** - Updates pre-commit hooks documentation
   - Monitors: `.pre-commit-config.yaml`, hook scripts, slash command definitions
   - Updates: Pre-commit hooks setup and configuration documentation

### How It Works

The automated documentation system works in three layers:

#### 1. Slash Commands (Agent Definitions)

Located in `.claude/commands/*.md`, these define the documentation agent behavior:

```bash
.claude/commands/
├── update-local-development-docs.md
├── update-ci-cd-docs.md
└── update-pre-commit-hooks.md
```

Each command:

- Specifies which files to monitor
- Defines which documentation to update
- Provides context and examples
- Sets exit behavior and timing constraints

#### 2. Manual Invocation

You can run these commands manually:

```bash
# Update local development documentation
airchat /update-local-development-docs

# Update CI/CD documentation
airchat /update-ci-cd-docs

# Update pre-commit hooks documentation
airchat /update-pre-commit-hooks
```

### Creating New Slash Commands

To add a new documentation automation command:

1. **Create the command file**:

   ```bash
   vim .claude/commands/update-my-docs.md
   ```

2. **Define the agent behavior**:

   ```markdown
   You are a documentation maintenance agent...

   ## Files to Monitor
   - `path/to/code/*.go`

   ## Documentation Files to Update
   - `_infra/docs/path/to/doc.md`

   ## Instructions
   ...
   ```

3. **Test the command**:

   ```bash
   airchat /update-my-docs
   ```

4. **Optionally add pre-commit integration** (see `pre-commit-hooks.md`)

## Best Practices

### For Code Changes

1. **Run documentation commands** after significant changes:

   ```bash
   airchat /update-local-development-docs
   ```

2. **Review AI-generated updates** before committing

3. **Add manual context** that AI might miss

4. **Update examples** to reflect new code

### For Documentation Changes

1. **Keep docs close to code** - Update docs in the same commit as code changes

2. **Test commands and examples** - Ensure all examples actually work

3. **Use consistent terminology** - Refer to components the same way throughout

4. **Add context** - Explain the "why", not just the "what"

5. **Link related docs** - Help users find related information

### For Slash Commands

1. **Be specific about triggers** - Clearly define which files to monitor

2. **Set clear exit behavior** - Commands should exit quickly when no changes needed

3. **Provide examples** - Show what updates should look like

4. **Keep commands focused** - Each command should have a single responsibility
