import pytest

from clipforge.post import queue
from clipforge.post.power import PowerAssertion
from tests.fakes import Clock, FakeCommands, fake_engine
from tests.posting import add_account, add_clip, at, custom


def caffeinate(seconds):
    return ["caffeinate", "-i", "-t", str(seconds)]


def test_power_assertion_holds_within_the_hour_and_releases():
    cmds = FakeCommands()
    power = PowerAssertion(cmds)
    power.update(None, at(6))
    power.update(at(7, 10), at(6))  # 70 minutes away
    assert cmds.calls == []
    power.update(at(7), at(6))  # an hour away: held until 15 minutes after it
    assert cmds.calls == [caffeinate(3600 + 900)]
    power.update(at(7), at(6, 1))
    power.update(at(6, 30), at(6, 1))  # an earlier post is already covered
    assert len(cmds.procs) == 1

    cmds.procs[0].returncode = 0  # it ended on its own
    power.update(at(7), at(6, 2))
    assert cmds.calls[-1] == caffeinate(3480 + 900)
    assert len(cmds.alive()) == 1

    power.update(at(7, 30), at(6, 40))  # a later post needs longer: one child, the new one
    assert cmds.calls[-1] == caffeinate(3000 + 900)
    assert cmds.alive() == [cmds.procs[-1]]

    power.update(None, at(6, 45))
    assert cmds.alive() == []
    power.update(at(6, 40), at(6, 45))  # past due: 15 minutes from now
    assert cmds.calls[-1] == caffeinate(900)
    power.release()
    assert cmds.alive() == []


def test_tick_reads_the_next_due_post():
    cmds, clock = FakeCommands(), Clock(at(6))
    engine = fake_engine(now=clock, run_cmd=cmds)
    try:
        custom(engine, add_clip(engine), add_account(engine), at(7, 10))
        queue.tick(engine)
        assert cmds.calls == []
        clock.set(at(6, 15))
        queue.tick(engine)
        assert cmds.calls == [["caffeinate", "-i", "-t", str(55 * 60 + 900)]]
        engine.posting.pause()
        assert cmds.alive() == []
        engine.posting.resume()
        assert len(cmds.alive()) == 1
    finally:
        engine.close()
    assert cmds.alive() == []


def test_tests_never_start_real_commands():
    from clipforge.post import power

    with pytest.raises(AssertionError):
        power.launch(["caffeinate", "-t", "1"])
