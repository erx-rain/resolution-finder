# Root conftest: pytest prepends this file's directory to sys.path, so
# `import resolution_finder` resolves no matter which directory pytest is
# invoked from. Without it, running pytest from elsewhere fails with
# `ModuleNotFoundError: No module named 'resolution_finder'`, which is
# indistinguishable from a genuine TDD red state.
