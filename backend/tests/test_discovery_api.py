"""HTTP-level tests for match discovery, favorites and the optional tournament field. Run with pytest."""
from .conftest import register, admin_headers


def discover(client, **params):
    r = client.get("/api/matches/discover", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_discover_returns_items_total_and_paging(client):
    a = discover(client, limit=10)
    assert len(a["items"]) == 10 and a["total"] == 85 and a["has_more"] is True and a["offset"] == 0
    b = discover(client, limit=10, offset=80)
    assert len(b["items"]) == 5 and b["has_more"] is False
    assert not {m["id"] for m in a["items"]} & {m["id"] for m in b["items"]}
    assert len(client.get("/api/matches").json()) <= 50          # the original endpoint still works the way it did


def test_discover_filters_and_sorting(client):
    sports = {s["name"]: s["id"] for s in client.get("/api/sports").json()}
    cricket = discover(client, sport_id=sports["Cricket"], limit=100)
    assert cricket["total"] > 0 and all(m["sport"]["name"] == "Cricket" for m in cricket["items"])
    cheap = discover(client, max_price=2000, sort="price_asc", limit=100)
    prices = [m["min_price"] for m in cheap["items"]]
    assert prices == sorted(prices) and all(p <= 2000 for p in prices)
    dear = discover(client, min_price=5000, sort="price_desc", limit=100)["items"]
    assert dear and all(m["max_price"] >= 5000 for m in dear)
    city = client.get("/api/stadiums").json()[0]["location"]
    assert all(m["stadium"]["location"] == city for m in discover(client, location=city, limit=100)["items"])
    assert discover(client, q="zzzz-no-such-team")["total"] == 0


def test_discover_rejects_bad_input_and_route_order_is_correct(client):
    assert client.get("/api/matches/discover", params={"sort": "bogus"}).status_code == 400
    assert client.get("/api/matches/discover", params={"min_price": 900, "max_price": 100}).status_code == 400
    assert client.get("/api/matches/discover", params={"limit": 0}).status_code == 422
    assert client.get("/api/matches/discover", params={"limit": 1000}).status_code == 422
    assert client.get("/api/matches/discover", params={"min_price": -5}).status_code == 422
    assert client.get("/api/matches/discover", params={"teams": ",".join(f"T{i}" for i in range(25))}).status_code == 400
    assert client.get("/api/matches/filters").status_code == 200        # not captured by /matches/{match_id}
    assert client.get("/api/matches/1").status_code == 200


def test_filter_options_endpoint(client):
    o = client.get("/api/matches/filters").json()
    assert len(o["sports"]) == 5 and o["cities"] and o["teams"] and o["price"]["min"] <= o["price"]["max"] and "price_asc" in o["sorts"] and o["tournaments"] == []


def test_admin_can_label_matches_with_a_tournament_and_it_becomes_filterable(client):
    ah, uh = admin_headers(client), register(client)
    m = client.get("/api/matches").json()[0]
    assert m["tournament"] is None
    assert client.put(f"/api/admin/matches/{m['id']}", headers=uh, json={"tournament": "IPL 2026"}).status_code == 403
    r = client.put(f"/api/admin/matches/{m['id']}", headers=ah, json={"tournament": "  IPL 2026  "})
    assert r.status_code == 200 and r.json()["tournament"] == "IPL 2026"
    assert [x["id"] for x in discover(client, tournament="ipl")["items"]] == [m["id"]]
    assert client.get("/api/matches/filters").json()["tournaments"] == ["IPL 2026"]
    assert client.put(f"/api/admin/matches/{m['id']}", headers=ah, json={"tournament": None}).json()["tournament"] is None
    assert discover(client, tournament="ipl")["total"] == 0
    assert client.put(f"/api/admin/matches/{m['id']}", headers=ah, json={"tournament": "x" * 81}).status_code == 422


def test_favorites_require_login_and_are_private(client):
    assert client.get("/api/favorites").status_code == 401
    assert client.put("/api/favorites/matches/1").status_code == 401
    h1, h2 = register(client), register(client, "u2@test.com")
    m = client.get("/api/matches").json()[0]
    r = client.put(f"/api/favorites/matches/{m['id']}", headers=h1)
    assert r.status_code == 200 and r.json()["match_ids"] == [m["id"]]
    assert client.put(f"/api/favorites/matches/{m['id']}", headers=h1).json()["match_ids"] == [m["id"]]        # idempotent
    assert client.put("/api/favorites/teams", headers=h1, params={"name": m["team_a"]}).json()["teams"] == [m["team_a"]]
    mine = client.get("/api/favorites", headers=h1).json()
    assert mine["match_ids"] == [m["id"]] and mine["teams"] == [m["team_a"]] and mine["matches"][0]["id"] == m["id"]
    other = client.get("/api/favorites", headers=h2).json()
    assert other["match_ids"] == [] and other["teams"] == [] and other["matches"] == []
    assert client.delete(f"/api/favorites/matches/{m['id']}", headers=h2).json()["match_ids"] == []            # cannot remove someone else's
    assert client.get("/api/favorites", headers=h1).json()["match_ids"] == [m["id"]]
    assert client.delete(f"/api/favorites/matches/{m['id']}", headers=h1).json()["match_ids"] == []
    assert client.delete("/api/favorites/teams", headers=h1, params={"name": m["team_a"]}).json()["teams"] == []


def test_favorites_validate_input(client):
    h = register(client)
    assert client.put("/api/favorites/matches/999999", headers=h).status_code == 404
    assert client.put("/api/favorites/matches/abc", headers=h).status_code == 422
    assert client.put("/api/favorites/teams", headers=h, params={"name": "No Such Team"}).status_code == 404
    assert client.put("/api/favorites/teams", headers=h).status_code == 422
    assert client.put("/api/favorites/teams", headers=h, params={"name": "x" * 61}).status_code == 422


def test_stadium_image_url_must_be_http_or_relative(client):
    ah = admin_headers(client)
    sp = client.get("/api/sports").json()[0]["id"]
    bad = client.post("/api/admin/stadiums", headers=ah, json={"name": "Bad Arena", "location": "Nowhere", "capacity": 100, "sport_id": sp, "image_url": "javascript:alert(1)"})
    assert bad.status_code == 422
    ok = client.post("/api/admin/stadiums", headers=ah, json={"name": "Good Arena", "location": "Somewhere", "capacity": 100, "sport_id": sp, "image_url": "https://example.com/a.jpg"})
    assert ok.status_code == 201 and ok.json()["image_url"] == "https://example.com/a.jpg"
