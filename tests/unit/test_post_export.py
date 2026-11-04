import pytest

from clipforge.post import store
from clipforge.post.errors import LifecycleError
from clipforge.post.export import ManualExportPublisher, exported_file, open_upload
from tests.fakes import Clock, FakeCommands, fake_engine
from tests.posting import add_account, add_clip, add_render, at, custom, jobs, post
from tests.unit.test_audio import edl_for

TIKTOK_UPLOAD = "https://www.tiktok.com/tiktokstudio/upload?from=upload&lang=en"


@pytest.fixture
def cmds():
    return FakeCommands()


@pytest.fixture
def engine(cmds):
    eng = fake_engine(now=Clock(at(6)), run_cmd=cmds)
    yield eng
    eng.close()


def test_manual_export_writes_video_and_caption_and_opens_the_folder(engine, cmds):
    account = add_account(engine, handle="money.clips")
    p = custom(engine, add_clip(engine), account, at(7, 10))  # 2 Nov in UTC, 3 Nov in Sydney
    folder = engine.posting.export(p.id)
    assert folder == engine.paths.exports / "money.clips" / "2026-11-03" / p.id
    assert (folder / "clip.mp4").read_bytes() == b"video 1"
    assert (folder / "caption.txt").read_text() == "TikTok words\n\n#money\n"
    assert cmds.calls == [["open", str(folder)]]
    assert post(engine, p.id).status == "exported"
    assert not jobs(engine, p.id)
    with pytest.raises(LifecycleError):
        engine.posting.export(p.id)


def test_export_credits_the_music_of_the_video_it_writes(engine):
    music = {"item_id": "l1", "title": "Night Drive", "artist": "Lena Hart", "db": -12.0}

    def with_credit(credit):
        return edl_for([(10.0, 40.0)]).model_copy(update={"music": {**music, "credit": credit}})

    clip = add_clip(engine, rendered=False)
    add_render(engine, clip, 1, edl=with_credit("Music: Night Drive by Lena Hart"))
    p = custom(engine, clip, add_account(engine), at(7, 10))
    add_render(engine, clip, 2, edl=with_credit("Music: Sunrise by Lena Hart"))  # rendered again since
    folder = engine.posting.export(p.id)
    assert (folder / "clip.mp4").read_bytes() == b"video 2"
    assert (folder / "caption.txt").read_text() == "TikTok words\n\nMusic: Sunrise by Lena Hart\n\n#money\n"


def test_export_folder_names_are_safe(engine):
    odd = add_account(engine, handle="../..//")
    p = custom(engine, add_clip(engine, 1), odd, at(7, 10))
    assert engine.posting.export(p.id) == engine.paths.exports / odd.id / "2026-11-03" / p.id
    dotted = add_account(engine, handle="..x y")
    q = custom(engine, add_clip(engine, 2), dotted, at(7, 30))
    assert engine.posting.export(q.id).parent.parent.name == "xy"


def test_manual_export_publisher_exports_the_post(engine, cmds):
    p = custom(engine, add_clip(engine), add_account(engine, handle="me"), at(7, 10))
    publisher = ManualExportPublisher(engine, "tiktok")
    assert (publisher.platform, publisher.method) == ("tiktok", "manual")
    assert publisher.check(store.get_account(engine.db(), p.account_id)).status == "connected"
    result = publisher.publish(p, None)
    assert result.external_id is None
    folder = engine.paths.exports / "me" / "2026-11-03" / p.id
    assert (folder / "clip.mp4").is_file()
    assert result.warnings == [f"Exported to {folder}"]


def test_export_keeps_its_folder(engine):
    account = add_account(engine, handle="money.clips")
    p = custom(engine, add_clip(engine), account, at(7, 10))
    folder = engine.posting.export(p.id)
    assert post(engine, p.id).state["export_dir"] == f"money.clips/2026-11-03/{p.id}"
    # Moved to another day, its account renamed: the post still finds the folder it was exported to.
    store.update_post(engine.db(), p.id, scheduled_at=at(9, day=5).timestamp())
    store.update_account(engine.db(), account.id, handle="renamed")
    assert exported_file(engine, post(engine, p.id)) == folder / "clip.mp4"
    # A stored folder outside exports/ is never followed.
    outside = engine.paths.exports.parent / "outside"
    outside.mkdir()
    (outside / "clip.mp4").write_bytes(b"video")
    store.update_post(engine.db(), p.id, state=post(engine, p.id).state | {"export_dir": "../outside"})
    assert exported_file(engine, post(engine, p.id)) is None


def test_open_upload_opens_the_page_and_reveals_the_file(engine, cmds):
    tiktok = custom(engine, add_clip(engine, 1), add_account(engine, "tiktok", "t0"), at(7, 10))
    insta = custom(engine, add_clip(engine, 2), add_account(engine, "instagram", "i0"), at(7, 30))
    shown = engine.posting.export(tiktok.id)
    gone = engine.posting.export(insta.id)
    cmds.procs.clear()
    open_upload(engine, tiktok.id)
    assert cmds.calls == [["open", TIKTOK_UPLOAD], ["open", "-R", str(shown / "clip.mp4")]]
    cmds.procs.clear()
    (gone / "clip.mp4").unlink()
    open_upload(engine, insta.id)
    assert cmds.calls == [["open", "https://www.instagram.com/"]]
