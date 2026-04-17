from typer.testing import CliRunner

from yaya import __version__
from yaya.__main__ import app

runner = CliRunner()


def test_version_import() -> None:
    assert isinstance(__version__, str)
    assert __version__


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_cli_hello() -> None:
    result = runner.invoke(app, ["hello", "--name", "yaya"])
    assert result.exit_code == 0
    assert "yaya" in result.stdout
