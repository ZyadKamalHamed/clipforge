import threading

from clipforge.events import Event, EventBus


def test_event_bus_fanout_and_drop_oldest():
    bus = EventBus()
    a = bus.subscribe()
    b = bus.subscribe(maxsize=2)
    for n in range(3):
        bus.publish("job", {"n": n})
    assert [a.get(timeout=0.1).data["n"] for _ in range(3)] == [0, 1, 2]
    first = b.get(timeout=0.1)
    assert isinstance(first, Event) and first.topic == "job" and first.ts > 0
    assert [first.data["n"], b.get(timeout=0.1).data["n"]] == [1, 2]  # the oldest was dropped
    assert b.get(timeout=0.01) is None
    b.close()
    bus.publish("job", {"n": 3})
    assert b.get(timeout=0.01) is None
    assert a.get(timeout=0.1).data["n"] == 3


def test_get_waits_for_a_publish_from_another_thread():
    bus = EventBus()
    sub = bus.subscribe()
    timer = threading.Timer(0.05, bus.publish, args=("project", {"id": "p1"}))
    timer.start()
    event = sub.get(timeout=2.0)
    assert event is not None and event.data == {"id": "p1"}


def test_close_wakes_a_waiting_get():
    bus = EventBus()
    sub = bus.subscribe()
    threading.Timer(0.05, sub.close).start()
    assert sub.get(timeout=2.0) is None
