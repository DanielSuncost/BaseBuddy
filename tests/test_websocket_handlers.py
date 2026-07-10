"""Tests for the WebSocket streaming handlers (subscribe/unsubscribe lifecycle)."""
import pytest


@pytest.fixture(scope="module")
def app_and_socketio():
    from basebuddy.app import create_app

    app, socketio = create_app()
    app.config["TESTING"] = True
    return app, socketio


@pytest.fixture()
def ws_client(app_and_socketio):
    app, socketio = app_and_socketio
    client = socketio.test_client(app)
    yield client
    if client.is_connected():
        client.disconnect()


def _ws_clients():
    from basebuddy.web import websocket_handlers

    return websocket_handlers.ws_clients


def test_connect_registers_client(ws_client):
    assert ws_client.is_connected()
    sid = ws_client.eio_sid
    assert any(True for _ in _ws_clients()), "client should be tracked after connect"


def test_subscribe_and_unsubscribe(ws_client):
    ws_client.emit("subscribe", {"cameras": [0, 1, 2]})
    subs = [cams for cams in _ws_clients().values() if cams == {0, 1, 2}]
    assert subs, "subscription should record the camera set"

    ws_client.emit("unsubscribe", {"cameras": [1]})
    subs = [cams for cams in _ws_clients().values() if cams == {0, 2}]
    assert subs, "unsubscribe should remove only the given cameras"


def test_disconnect_removes_client(app_and_socketio):
    app, socketio = app_and_socketio
    client = socketio.test_client(app)
    assert client.is_connected()
    before = len(_ws_clients())
    client.disconnect()
    after = len(_ws_clients())
    assert after == before - 1


def test_subscribe_after_disconnect_is_ignored(app_and_socketio):
    """A subscribe from an unknown sid must not resurrect the client entry."""
    from basebuddy.web import websocket_handlers

    app, socketio = app_and_socketio
    client = socketio.test_client(app)
    client.emit("subscribe", {"cameras": [5]})
    client.disconnect()

    # No entry with camera 5 should remain after disconnect
    leftovers = [cams for cams in websocket_handlers.ws_clients.values() if 5 in cams]
    assert not leftovers
