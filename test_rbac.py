import requests
from getpass import getpass

BASE_URL = "http://127.0.0.1:8002"

ENDPOINTS = [
    "/auth/me",
    "/jobs/active",
    "/jobs/history",
    "/cluster/node",
    "/ui",
]


def test_user(username):
    print(f"\n{'=' * 45}")
    print(f"Testing: {username}")
    print(f"{'=' * 45}")

    session = requests.Session()

    password = getpass(f"Portal password for {username}: ")

    try:
        login = session.post(
            f"{BASE_URL}/auth/login",
            json={
                "username": username,
                "password": password,
            },
            timeout=10,
        )
    finally:
        del password

    print(f"Login: HTTP {login.status_code}")

    if login.status_code != 200:
        print("Login failed.")
        return

    for endpoint in ENDPOINTS:
        try:
            response = session.get(
                f"{BASE_URL}{endpoint}",
                timeout=10,
            )

            print(
                f"{endpoint:<20} -> "
                f"HTTP {response.status_code}"
            )

        except requests.RequestException as exc:
            print(f"{endpoint:<20} -> ERROR: {exc}")

    me = session.get(
        f"{BASE_URL}/auth/me",
        timeout=10,
    )

    if me.ok:
        csrf_token = me.json()["csrf_token"]

        logout = session.post(
            f"{BASE_URL}/auth/logout",
            headers={
                "X-Portal-CSRF": csrf_token,
            },
            timeout=10,
        )

        print(f"Logout: HTTP {logout.status_code}")

        after_logout = session.get(
            f"{BASE_URL}/auth/me",
            timeout=10,
        )

        print(
            "After logout: "
            f"HTTP {after_logout.status_code}"
        )


test_user("lailai")
test_user("mangkud")