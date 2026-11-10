{
  languages.python = {
    enable = true;
    version = "3.11";

    uv = {
      enable = true;
      sync = {
        enable = true;
        allExtras = true;
      };
    };
  };

  scripts = {
    run-ruff.exec = "uv run ruff check --fix";
    run-tests.exec = "uv run pytest tests";
  };
}
