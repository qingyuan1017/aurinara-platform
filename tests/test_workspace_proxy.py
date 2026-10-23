from aurinara_platform.workspace_proxy import (
    WORKSPACE_SESSION_COOKIE,
    _request_headers,
    _response_headers,
)


def test_proxy_strips_platform_credentials_and_hop_headers() -> None:
    headers = _request_headers(
        [
            ("Authorization", "Bearer secret"),
            ("Connection", "keep-alive"),
            ("Cookie", f"theme=dark; {WORKSPACE_SESSION_COOKIE}=proxy-secret"),
            ("X-Request-ID", "request-1"),
        ]
    )

    assert headers == {"Cookie": "theme=dark", "X-Request-ID": "request-1"}


def test_proxy_rewrites_absolute_and_relative_redirects_and_drops_cookies() -> None:
    absolute = _response_headers(
        [("Location", "http://10.0.1.20:8080/login?next=%2F"), ("Set-Cookie", "auth=x")],
        "http://10.0.1.20:8080",
        "/v1/workspaces/id/ide",
    )
    relative = _response_headers(
        [("Location", "/welcome")],
        "http://10.0.1.20:8080",
        "/v1/workspaces/id/ide",
    )

    assert absolute == {"Location": "/v1/workspaces/id/ide/login?next=%2F"}
    assert relative == {"Location": "/v1/workspaces/id/ide/welcome"}
