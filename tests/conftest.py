import os
from collections.abc import AsyncGenerator

# Environment variables config for test
os.environ["DATABASE_URL"] = "postgresql+psycopg://dracula:eldorado@localhost/test_algorithm_and_blues"
os.environ["SECRET_KEY"] = "test-secret-key-for-testing-only"

# GitHub config for test
os.environ["PERSONAL_ACCESS_TOKEN"] = "test-github-token"
os.environ["REPO_OWNER"] = "test-owner"
os.environ["REPO_NAME"] = "test-repo"
os.environ["BRANCH"] = "main"

# Imports
import pytest
import respx
import httpx
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from database import Base, get_db
from main import app

pytest_plugins = ["anyio"]


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session")
def test_engine():
    engine = create_async_engine(
        os.environ["DATABASE_URL"],
        poolclass=NullPool,
    )
    return engine


@pytest.fixture(scope="session")
async def setup_db(test_engine):
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await test_engine.dispose()


@pytest.fixture
async def db_session(
        test_engine,
        setup_db,
) -> AsyncGenerator[AsyncSession]:
    conn = await test_engine.connect()
    trans = await conn.begin()

    test_async_session = async_sessionmaker(
        bind=conn,
        class_=AsyncSession,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )

    async with test_async_session() as session:
        try:
            yield session
        finally:
            await session.close()
            await trans.rollback()
            await conn.close()


# Fixture to mock GitHub calls (respx)
@pytest.fixture
def mocked_github():
    """
    A fix that enables respx and provides methods to easily mock
    the GitHub endpoints used by github_storage.
    """
    with respx.mock(
            base_url="https://api.github.com",
            assert_all_called=False,  # Do not fail if some routes are not called
            assert_all_mocked=False,  # Allows to mock only certain routes
    ) as respx_mock:
        yield respx_mock


# FastAPI client fix (identical to the original)
@pytest.fixture
async def client(
    db_session: AsyncSession,
    mocked_github, # Add the GitHub mock so that it is active during testing
) -> AsyncGenerator[AsyncClient]:
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()

# Helpers to create a user and get a token
async def create_test_user(
        client: AsyncClient,
        username: str = "testuser",
        email: str = "test@example.com",
        password: str = "testpassword123",
) -> dict:
    response = await client.post(
        "/api/users",
        json={
            "username": username,
            "email": email,
            "password": password,
        },
    )
    assert response.status_code == 201, f"Failed to create user: {response.text}"
    return response.json()

async def login_user(
       client: AsyncClient,
        email: str = "test@example.com",
        password: str = "testpassword123",
) -> str:
    response = await client.post(
        "/api/users/token",
        data={"username": email, "password": password},
    )
    assert response.status_code == 200, f"Failed to login: {response.text}"
    return response.json()["access_token"]

def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}