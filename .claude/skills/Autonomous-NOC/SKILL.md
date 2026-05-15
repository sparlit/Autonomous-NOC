```markdown
# Autonomous-NOC Development Patterns

> Auto-generated skill from repository analysis

## Overview
This skill provides guidance on the development patterns and conventions used in the Autonomous-NOC TypeScript codebase. It covers file naming, import/export styles, commit message conventions, and testing patterns. By following these guidelines, contributors can ensure consistency and maintainability across the project.

## Coding Conventions

### File Naming
- Use **camelCase** for file names.
  - Example: `networkManager.ts`, `deviceConfig.ts`

### Import Style
- Use **alias imports** for modules.
  - Example:
    ```typescript
    import db from 'src/database'
    import utils from 'src/helpers/utils'
    ```

### Export Style
- Use **default exports** for modules.
  - Example:
    ```typescript
    const deviceManager = { ... }
    export default deviceManager
    ```

### Commit Message Conventions
- Use **conventional commits** with the `feat` prefix for new features.
- Commit messages are concise, averaging 68 characters.
  - Example:
    ```
    feat: add SNMP polling for network devices
    ```

## Workflows

### Feature Development
**Trigger:** When adding a new feature or module  
**Command:** `/feature-development`

1. Create a new file using camelCase naming.
2. Implement the feature using TypeScript.
3. Use alias imports for dependencies.
4. Export the main functionality as a default export.
5. Write corresponding tests in a `.test.ts` file.
6. Commit changes using the `feat:` prefix and a concise description.

### Testing
**Trigger:** When writing or running tests  
**Command:** `/run-tests`

1. Create test files matching the pattern `*.test.ts`.
2. Implement tests for each module or feature.
3. Run the test suite using the project's test runner (framework unknown; refer to project scripts).
4. Ensure all tests pass before merging changes.

## Testing Patterns

- Test files follow the `*.test.ts` naming convention.
  - Example: `networkManager.test.ts`
- The specific testing framework is not detected; check the project documentation or scripts for details.
- Place tests alongside the modules they cover or in a dedicated `tests` directory.

## Commands

| Command               | Purpose                                   |
|-----------------------|-------------------------------------------|
| /feature-development  | Start a new feature using project patterns|
| /run-tests            | Run the test suite                        |
```