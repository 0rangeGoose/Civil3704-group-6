# Contributing to the Transport AR Project

Thank you for contributing to this project! This guide will help your team work together effectively.

## Getting Started

1. **Clone the repository**

   ```bash
   git clone <repository-url>
   cd template-repository
   ```

2. **Set up your environment**

   ```bash
   make setup
   make install
   ```

3. **Create a feature branch**

   ```bash
   git checkout -b feature/your-feature-name
   ```

## Development Workflow

### Branch Strategy

- `main` - Stable, working code only
- `develop` - Integration branch for features
- `feature/*` - Individual features
- `bugfix/*` - Bug fixes
- `docs/*` - Documentation updates

### Making Changes

1. **Create a feature branch** from `develop`
2. **Make your changes** in small, logical commits
3. **Test your changes** with `make test`
4. **Update documentation** if needed
5. **Create a pull request** to `develop`

### Commit Messages

Write clear, descriptive commit messages:

```
<type>: <subject>

<body>

<footer>
```

**Types:**

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `style:` Formatting, missing semicolons, etc.
- `refactor:` Code restructuring
- `test:` Adding tests
- `chore:` Maintenance tasks

**Examples:**

```
feat: add GTFS-Realtime vehicle position fetching

Implemented GTFSRealtime class to fetch and parse vehicle positions
from the transit agency's real-time feed. Added error handling and
retry logic.

Closes #12
```

## Code Standards

### Python

- Follow [PEP 8](https://pep8.org/) style guide
- Use type hints where appropriate
- Maximum line length: 100 characters
- Use docstrings for all functions and classes

```python
def calculate_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate distance between two geographic coordinates.
    
    Args:
        lat1: Latitude of first point
        lon1: Longitude of first point
        lat2: Latitude of second point
        lon2: Longitude of second point
    
    Returns:
        Distance in kilometers
    """
    # Implementation here
    pass
```

### JavaScript/TypeScript

- Use ES6+ features
- Follow Airbnb style guide
- Use meaningful variable names
- Add JSDoc comments for functions

```javascript
/**
 * Calculate distance between two coordinates
 * @param {number} lat1 - Latitude of first point
 * @param {number} lon1 - Longitude of first point
 * @param {number} lat2 - Latitude of second point
 * @param {number} lon2 - Longitude of second point
 * @returns {number} Distance in kilometers
 */
function calculateDistance(lat1, lon1, lat2, lon2) {
    // Implementation here
}
```

### General Guidelines

- Write self-documenting code
- Keep functions small and focused
- Avoid magic numbers (use named constants)
- Handle errors gracefully
- Add comments for complex logic
- Don't commit commented-out code

## Testing

### Writing Tests

- Test all new features
- Test edge cases
- Test error conditions
- Aim for >70% code coverage

### Running Tests

```bash
make test          # Run all tests
make test-verbose  # Run with detailed output
```

## Pull Request Process

1. **Update your branch** with latest `develop`

   ```bash
   git checkout develop
   git pull origin develop
   git checkout your-feature-branch
   git merge develop
   ```

2. **Ensure tests pass** and code is clean

   ```bash
   make test
   make lint  # If you have linting set up
   ```

3. **Push your branch**

   ```bash
   git push origin your-feature-branch
   ```

4. **Create pull request** on GitHub/GitLab
   - Provide clear description
   - Reference any related issues
   - Request review from team members

5. **Address review comments**
   - Make requested changes
   - Push updates to same branch
   - Reply to comments

6. **Merge** after approval
   - Squash commits if needed
   - Delete feature branch after merge

## Project Structure Guidelines

### Adding New Modules

```
src/
├── module_name/
│   ├── __init__.py
│   ├── core.py          # Main logic
│   ├── utils.py         # Helper functions
│   └── tests/           # Module tests
│       └── test_core.py
```

### File Naming

- Python: `lowercase_with_underscores.py`
- JavaScript: `camelCase.js` or `PascalCase.js` for classes
- Config: `lowercase-with-hyphens.json`

## Documentation

### Code Documentation

- Document all public functions/classes
- Explain "why" not just "what"
- Include examples for complex functionality
- Keep comments up-to-date with code

### Project Documentation

Update these files as needed:

- `README.md` - Project overview and setup
- `docs/ARCHITECTURE.md` - System design
- `src/README.md` - Implementation details

## Team Collaboration

### Communication

- Use GitHub/GitLab issues for task tracking
- Comment on PRs for code review
- Update team regularly on progress
- Ask for help when stuck!

### Code Review

When reviewing PRs:

- Be constructive and respectful
- Check for correctness and clarity
- Verify tests are included
- Ensure documentation is updated
- Look for potential bugs or edge cases

When receiving feedback:

- Don't take it personally
- Ask questions if unclear
- Make requested changes promptly
- Learn from the feedback

### Pair Programming

Consider pair programming for:

- Complex features
- Debugging difficult issues
- Learning new technologies
- Onboarding new team members

## Common Issues

### Merge Conflicts

```bash
git checkout develop
git pull origin develop
git checkout your-branch
git merge develop
# Resolve conflicts in your editor
git add .
git commit -m "Merge develop and resolve conflicts"
```

### Reverting Changes

```bash
git revert <commit-hash>  # Revert specific commit
git reset --hard HEAD~1   # Undo last commit (careful!)
```

## Getting Help

- Check existing documentation
- Search GitHub issues
- Ask your team members
- Consult your instructor/TA
- Review GTFS documentation at <https://gtfs.org>

## License

This project is for educational purposes. Consult with your instructor regarding licensing.
