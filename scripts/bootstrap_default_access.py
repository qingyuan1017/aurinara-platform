"""Install deterministic self-service study access defaults."""

from aurinara_platform.config import get_settings
from aurinara_platform.database import create_session_factory
from aurinara_platform.default_access import ensure_platform_access_defaults
from aurinara_platform.models import PolicyState, utc_now


def main() -> None:
    factory = create_session_factory(get_settings())
    with factory.begin() as session:
        changed = ensure_platform_access_defaults(session)
        state = session.get(PolicyState, 1)
        if state is None:
            raise RuntimeError("Platform policy state is not initialized")
        if changed:
            state.version += 1
            state.updated_at = utc_now()
    print(f"default_access={'updated' if changed else 'ready'}")


if __name__ == "__main__":
    main()
