"""Mini App раздаётся бэкендом (без БД)."""


async def test_webapp_served():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/app/")
        assert r.status_code == 200 and "max-web-app.js" in r.text
        for name in ("core.js", "app.js", "cabinet.js"):
            assert f"{name}?v=" in r.text
            assert (await c.get(f"/app/{name}")).status_code == 200
        assert "initDataUnsafe" in (await c.get("/app/core.js")).text
        r = await c.get("/app", follow_redirects=False)
        assert r.status_code in (301, 307, 308)
