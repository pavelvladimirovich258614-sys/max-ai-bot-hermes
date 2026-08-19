from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CERTS = ROOT / "certs"
INSTALLED_CERTS = Path("/usr/local/share/ca-certificates")


def test_official_russian_ca_files_are_present():
    source_present = (
        (SOURCE_CERTS / "russian_trusted_root_ca_pem.crt").exists()
        and (SOURCE_CERTS / "russian_trusted_sub_ca_2024_pem.crt").exists()
    )
    installed_present = (
        (INSTALLED_CERTS / "russian_trusted_root_ca.crt").exists()
        and (INSTALLED_CERTS / "russian_trusted_sub_ca_2024.crt").exists()
    )
    assert source_present or installed_present


def test_dockerfile_or_runtime_installs_russian_ca_chain():
    dockerfile = ROOT / "Dockerfile"
    if dockerfile.exists():
        text = dockerfile.read_text(encoding="utf-8")
        assert "ca-certificates" in text
        assert "russian_trusted_root_ca_pem.crt" in text
        assert "russian_trusted_sub_ca_2024_pem.crt" in text
        assert "update-ca-certificates" in text
    else:
        assert (INSTALLED_CERTS / "russian_trusted_root_ca.crt").exists()
        assert (INSTALLED_CERTS / "russian_trusted_sub_ca_2024.crt").exists()
