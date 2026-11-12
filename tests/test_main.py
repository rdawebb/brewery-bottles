"""Tests for the main entry point."""

import pytest

from bottles.__main__ import main


def test_no_command_is_a_usage_error() -> None:
    """Test that running `bottles` with no command prints usage and exits with code 2."""
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
