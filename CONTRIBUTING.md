# Contributing

Keep changes focused on the plugin or the optional sidecar, and keep runtime
state outside the checkout. Before opening a pull request, run:

```powershell
python -m unittest discover -s plugins/context-tree/tests -p "test_*.py" -v
python -m unittest discover -s sidecar -p "test_*.py" -v
python -m compileall -q plugins/context-tree/scripts sidecar
```

Do not commit `.context-tree/`, credentials, `.env` files, databases, logs, or
generated screenshots. New sidecar behavior should include a unit test that
does not require a live PostgreSQL instance.
