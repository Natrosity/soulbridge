"""Pure-function tests for the Plex sign-in helpers (no network).
Runnable with pytest or `python tests/test_plextv.py`."""
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from soulbridge.clients import plextv as p  # noqa: E402

MACHINE = "abc123servermachineid"


def test_auth_url_carries_params_in_fragment():
    url = p.auth_url("cid-xyz", "PINCODE", "https://sb.example.com/auth/plex/callback?state=s1")
    assert url.startswith("https://app.plex.tv/auth#?")
    frag = url.split("#?", 1)[1]
    q = urllib.parse.parse_qs(frag)
    assert q["clientID"] == ["cid-xyz"]
    assert q["code"] == ["PINCODE"]
    assert q["forwardUrl"] == ["https://sb.example.com/auth/plex/callback?state=s1"]
    assert q["context[device][product]"] == ["Soulbridge"]


def test_member_when_server_owned():
    res = [{"clientIdentifier": MACHINE, "provides": "server", "owned": True}]
    assert p.server_in_resources(res, MACHINE) is True


def test_member_when_server_shared():
    # a shared (not owned) server still grants access
    res = [{"clientIdentifier": MACHINE, "provides": "server", "owned": False}]
    assert p.server_in_resources(res, MACHINE) is True


def test_not_member_when_no_matching_server():
    res = [{"clientIdentifier": "someone-elses-server", "provides": "server"}]
    assert p.server_in_resources(res, MACHINE) is False


def test_not_member_when_resource_is_not_a_server():
    # a Plex client/player that shares the id but provides no server must not count
    res = [{"clientIdentifier": MACHINE, "provides": "client,player"}]
    assert p.server_in_resources(res, MACHINE) is False


def test_not_member_with_blank_machine_id():
    res = [{"clientIdentifier": "", "provides": "server"}]
    assert p.server_in_resources(res, "") is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print(f"\n{len(fns)} passed")
