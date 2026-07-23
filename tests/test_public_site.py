from pathlib import Path

from public_site import infer_caddy_site_url, resolve_public_site_url


def test_infers_private_brain_route_in_correct_https_site():
    caddy = """
    :8080 {
        handle_path /private1234567890/* {
            reverse_proxy 127.0.0.1:8000
        }
    }
    https://ombre.example.test:8443 {
        bind 127.0.0.1
        handle_path /private1234567890/* {
            reverse_proxy 127.0.0.1:8000
        }
        handle_path /report/* {
            reverse_proxy 127.0.0.1:9999
        }
    }
    """
    assert infer_caddy_site_url(caddy) == (
        "https://ombre.example.test:8443/private1234567890"
    )


def test_explicit_site_url_wins_without_repository_default(tmp_path: Path):
    caddy = tmp_path / "Caddyfile"
    caddy.write_text("", encoding="utf-8")
    assert resolve_public_site_url(
        {"OMBRE_SITE_URL": "https://example.test/private/"}, caddy
    ) == "https://example.test/private"
    assert resolve_public_site_url({}, caddy) == ""


def test_render_is_only_used_when_render_provides_it(tmp_path: Path):
    caddy = tmp_path / "missing"
    assert resolve_public_site_url(
        {"RENDER_EXTERNAL_URL": "https://current-service.onrender.com/"}, caddy
    ) == "https://current-service.onrender.com"
