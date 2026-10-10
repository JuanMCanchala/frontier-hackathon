import pytest

from aprendiz.redact import REDACTED, redact

SECRETS = ["hunter2-pass", "team-token-abc", "or-key-xyz"]


def test_exact_secrets_are_removed() -> None:
    text = "clave hunter2-pass, token team-token-abc, key or-key-xyz"
    out = redact(text, SECRETS)
    assert not any(s in out for s in SECRETS)
    assert out.count(REDACTED) == 3


def test_credential_shapes_are_removed() -> None:
    out = redact("key sk-or-v1-abcdef0123456789 y Bearer abc.def-123", [])
    assert "sk-or-v1" not in out
    assert "abc.def-123" not in out
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.abc123_-def"
    assert jwt not in redact(f"token {jwt}", [])
    for tok in [
        "ghp_" + "a1" * 15,
        "github_pat_" + "B2" * 15,
        "AKIA" + "ABCD1234" * 2,
        "xoxb-123456789012-abcdef",
    ]:
        assert tok not in redact(f"x {tok} y", [])


@pytest.mark.parametrize(
    "value",
    [
        "901234567",
        "3157654321",
        "45",
        "1.250.000",
        "14/10/2026",
        "123456789012345678901234",
        "FV2024A0012",
        "PED2026091500012345ABCDEFG",
        "1019876543",
        "CL-0007",
    ],
)
def test_normal_values_pass_through(value: str) -> None:
    text = f"Resultado: {value}"
    assert redact(text, SECRETS) == text


def test_empty_secret_is_ignored() -> None:
    assert redact("hola", [""]) == "hola"
