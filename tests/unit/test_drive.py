"""Google Drive links (SPEC 7.1): a file link is a source like a YouTube link, with no Allow other sites
needed, and a folder link adds a project for each video in it. Drive's errors say what to do in Drive's
terms: share the file as Anyone with the link, or try again later when Google's download limit is hit.
Nothing here reaches Google: yt-dlp is a stand-in, and the folder extractor's two requests are stubbed."""

import pytest
import yt_dlp
from yt_dlp.extractor.googledrive import GoogleDriveIE

from clipforge.campaigns.models import Footage
from clipforge.campaigns.service import NO_FILES, footage_allows
from clipforge.errors import ClipForgeError, NetworkError, SourceRefused
from clipforge.ingest import ytdlp
from clipforge.ingest.urls import (
    DRIVE_CUT,
    DRIVE_EMPTY,
    FOLDER_MAX,
    Rejected,
    drive_file_url,
    drive_folder_url,
)
from clipforge.ingest.ytdlp import (
    DRIVE_FOLDER_PRIVATE,
    DRIVE_PRIVATE,
    DriveFile,
    DriveFolder,
    DriveFolderIE,
    YtDlpClient,
    drive_error,
)
from clipforge.models import ProjectOptions
from tests.fakes import fake_engine
from tests.posting import add_campaign
from tests.unit.test_ytdlp import FakeYDL, no_cookies

ID_A = "1AaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaA"
ID_B = "1BbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbB"
FOLDER_ID = "1FoLdErIdAbCdEfGhIjKlMnOpQrStUvWx"
FILE = drive_file_url(ID_A)
FOLDER = drive_folder_url(FOLDER_ID)
YOUTUBE = "https://www.youtube.com/watch?v=arj7oStGLkU"


@pytest.fixture
def engine():
    eng = fake_engine()
    yield eng
    eng.close()


# What Drive's errors say


@pytest.mark.parametrize(
    "said",
    [
        f"ERROR: [GoogleDrive] {ID_A}: Unable to download JSON metadata: HTTP Error 403: Forbidden",
        f"ERROR: [GoogleDrive] {ID_A}: Unable to download JSON metadata: HTTP Error 404: Not Found",
        f"ERROR: [GoogleDrive] {ID_A}: Unable to download JSON metadata: HTTP Error 401: Unauthorized",
    ],
)
def test_drive_private_file_says_share_it(said):
    with pytest.raises(SourceRefused) as err:
        YtDlpClient(ydl_class=FakeYDL(error=said)).fetch_info(FILE)
    assert err.value.code == "private"  # a refusal, as for a private YouTube video
    assert err.value.message == DRIVE_PRIVATE
    assert DRIVE_PRIVATE == (
        "This Drive file isn't shared as Anyone with the link. Share it that way, or ask whoever sent "
        "the link to, then add it again."
    )
    assert err.value.details == said


@pytest.mark.parametrize(
    "said",
    [
        "ERROR: [GoogleDrive] x: Too many users have viewed or downloaded this file recently.",
        "ERROR: unable to download video data: HTTP Error 429: Too Many Requests",
        "ERROR: [GoogleDrive] x: The download quota for this file has been exceeded",
    ],
)
def test_drive_download_limit_says_try_again_later(tmp_path, said):
    with pytest.raises(ClipForgeError) as err:
        YtDlpClient(ydl_class=FakeYDL(error=said)).download_video(FILE, tmp_path, "best")
    assert err.value.code == "drive_quota"
    assert "Try again later" in err.value.message
    assert "YouTube" not in err.value.message


def test_drive_network_and_unknown_errors_name_drive():
    net = drive_error("ERROR: [GoogleDrive] x: Unable to download JSON metadata: <urlopen error timed out>")
    assert isinstance(net, NetworkError) and net.retryable
    assert "Google Drive" in net.message and "YouTube" not in net.message
    other = drive_error("ERROR: [GoogleDrive] x: Something new went wrong")
    assert other.code == "ytdlp_failed"
    assert "Google Drive" in other.message and "YouTube" not in other.message


def test_youtube_errors_keep_their_youtube_wording():
    with pytest.raises(SourceRefused) as err:
        YtDlpClient(ydl_class=FakeYDL(error="ERROR: [youtube] x: Private video")).fetch_info(YOUTUBE)
    assert err.value.message == ytdlp.REFUSED["private"]


# Listing a folder


def folder_listing(*entries, **changes) -> dict:
    return {"_type": "playlist", "id": FOLDER_ID, "title": "Brief videos", "entries": list(entries)} | changes


def entry(file_id: str, title: str | None = None) -> dict:
    found = {"_type": "url", "url": f"https://drive.google.com/file/d/{file_id}", "ie_key": "GoogleDrive"}
    return found | ({"id": file_id, "title": title} if title is not None else {})


def test_list_drive_folder_reads_one_flat_listing_with_its_own_extractor():
    fake = FakeYDL(folder_listing(entry(ID_A, "Summer drop.mp4"), entry(ID_B, "Teaser.mov")))
    folder = YtDlpClient(ydl_class=fake).list_drive_folder(FOLDER, 10)
    assert folder == DriveFolder(
        "Brief videos", [DriveFile(ID_A, "Summer drop"), DriveFile(ID_B, "Teaser")], False
    )
    assert fake.calls == [(FOLDER, False)]
    assert (fake.opts["extract_flat"], fake.opts["playlistend"]) == ("in_playlist", 11)
    assert [type(ie) for ie in fake.extractors] == [DriveFolderIE]
    assert fake.ie_key == DriveFolderIE.ie_key()
    assert no_cookies(fake.opts)


def test_list_drive_folder_takes_ids_from_the_links_when_entries_have_none():
    fake = FakeYDL(folder_listing(entry(ID_A), {"_type": "url", "url": "https://example.net/x"}))
    assert YtDlpClient(ydl_class=fake).list_drive_folder(FOLDER, 10).files == [DriveFile(ID_A, "")]


def test_list_drive_folder_says_when_there_are_more():
    many = [entry(f"1{n:032d}", f"Clip {n}.mp4") for n in range(4)]
    folder = YtDlpClient(ydl_class=FakeYDL(folder_listing(*many))).list_drive_folder(FOLDER, 3)
    assert [f.title for f in folder.files] == ["Clip 0", "Clip 1", "Clip 2"]
    assert folder.more is True


@pytest.mark.parametrize(
    "said",
    [
        f"ERROR: [GoogleDrive:Folder] {FOLDER_ID}: Unable to download webpage: HTTP Error 404: Not Found",
        f"ERROR: [GoogleDrive:Folder] {FOLDER_ID}: Unable to download webpage: HTTP Error 403: Forbidden",
        f"ERROR: [GoogleDrive:Folder] {FOLDER_ID}: Unable to extract key; please report this issue",
        f"ERROR: [GoogleDrive:Folder] {FOLDER_ID}: Unable to download webpage: HTTP Error 400: Bad Request",
    ],
)
def test_private_drive_folder_says_share_it(said):
    with pytest.raises(SourceRefused) as err:
        YtDlpClient(ydl_class=FakeYDL(error=said)).list_drive_folder(FOLDER, 10)
    assert (err.value.code, err.value.message) == ("private", DRIVE_FOLDER_PRIVATE)


# The folder extractor: yt-dlp's own, keeping each item's name and leaving out what isn't a video

KEY = "AIza" + "k" * 35
ITEMS = [
    {"id": ID_A, "title": "Summer drop.mp4", "mimeType": "video/mp4"},
    {"id": "1" + "p" * 32, "title": "Brief.pdf", "mimeType": "application/pdf"},
    {"id": "1" + "f" * 32, "title": "Raw", "mimeType": "application/vnd.google-apps.folder"},
    {"id": ID_B, "title": "Teaser.mov", "mimeType": "video/quicktime"},
    {"id": "1" + "i" * 32, "title": "Logo.png", "mimeType": "image/png"},
]


def test_folder_extractor_keeps_only_the_videos_with_their_names(monkeypatch):
    ie = DriveFolderIE(yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}))  # never goes online here
    keys = []

    def call_api(folder_id, key, data, **kwargs):
        keys.append(key)
        return {"title": "Brief videos"} if f"/files/{folder_id} HTTP" in data else {"items": ITEMS}

    monkeypatch.setattr(ie, "_download_webpage", lambda *a, **k: f'<script>init("{KEY}")</script>')
    monkeypatch.setattr(ie, "_call_api", call_api)
    result = ie._real_extract(FOLDER)
    entries = list(result["entries"])
    assert result["title"] == "Brief videos"
    assert [(e["id"], e["title"], e["url"]) for e in entries] == [
        (ID_A, "Summer drop.mp4", drive_file_url(ID_A)),
        (ID_B, "Teaser.mov", drive_file_url(ID_B)),
    ]
    assert keys and set(keys) == {KEY}


def test_folder_extractor_without_item_details_keeps_every_link():
    link = f"https://drive.google.com/file/d/{ID_A}"
    result = DriveFolderIE().playlist_from_matches([link], FOLDER_ID, "Brief videos", ie=GoogleDriveIE)
    assert [e["url"] for e in result["entries"]] == [link]


# Adding Drive links


def test_a_drive_file_link_needs_no_other_sites_and_is_added_once(engine):
    assert engine.settings.allow_other_sites is False
    (first,), _ = engine.add_sources(f"https://drive.google.com/open?id={ID_A}", ProjectOptions())
    (again,), _ = engine.add_sources(f"https://drive.google.com/file/d/{ID_A}/view", ProjectOptions())
    assert (first.source_kind, first.source_url, first.video_id) == ("drive", FILE, ID_A)
    assert again.id == first.id


def test_a_drive_folder_link_adds_a_project_for_each_video(engine):
    files = [DriveFile(ID_A, "Summer drop"), DriveFile(ID_B, "Teaser")]
    engine.youtube.folder = DriveFolder("Brief videos", files, False)
    text = f"https://drive.google.com/drive/folders/{FOLDER_ID}?usp=sharing"
    projects, rejected = engine.add_sources(text, ProjectOptions(as_is=True))
    assert rejected == []
    assert [(p.source_kind, p.source_url, p.video_id, p.title) for p in projects] == [
        ("drive", FILE, ID_A, "Summer drop"),
        ("drive", drive_file_url(ID_B), ID_B, "Teaser"),
    ]
    assert all(p.options.as_is for p in projects)  # each takes the options asked for
    assert engine.youtube.called("list_drive_folder") == [("list_drive_folder", FOLDER, FOLDER_MAX)]


def test_a_private_drive_folder_is_listed_as_not_used(engine):
    engine.youtube.folder = SourceRefused("private", DRIVE_FOLDER_PRIVATE)
    projects, rejected = engine.add_sources(f"{FOLDER} {drive_file_url(ID_B)}", ProjectOptions())
    assert [p.video_id for p in projects] == [ID_B]  # the folder's trouble doesn't stop the other links
    assert rejected == [Rejected(FOLDER, DRIVE_FOLDER_PRIVATE)]


def test_an_empty_drive_folder_says_so(engine):
    engine.youtube.folder = DriveFolder("Nothing yet", [], False)
    projects, rejected = engine.add_sources(FOLDER, ProjectOptions())
    assert projects == []
    assert rejected == [Rejected(FOLDER, DRIVE_EMPTY)]


def test_a_big_drive_folder_adds_the_first_videos_and_says_so(engine):
    files = [DriveFile(f"1{n:032d}", f"Clip {n}") for n in range(FOLDER_MAX + 1)]
    engine.youtube.folder = DriveFolder("Everything", files, False)
    projects, rejected = engine.add_sources(FOLDER, ProjectOptions())
    assert len(projects) == FOLDER_MAX
    assert rejected == [Rejected(FOLDER, DRIVE_CUT.format(n=FOLDER_MAX))]


# Which file comes down


COPIES = [  # the shape of what yt-dlp's Drive extractor gives for a 1080 x 1920 upload: Google's copies
    {
        "format_id": "18",
        "ext": "mp4",
        "width": 360,
        "height": 640,
        "vcodec": "avc1.42001E",
        "acodec": "mp4a.40.2",
    },
    {
        "format_id": "22",
        "ext": "mp4",
        "width": 720,
        "height": 1280,
        "vcodec": "avc1.64001F",
        "acodec": "mp4a.40.2",
    },
    {
        "format_id": "137",
        "ext": "mp4",
        "width": 1080,
        "height": 1920,
        "vcodec": "avc1.640028",
        "acodec": "none",
    },
    {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"},
]
ORIGINAL = {"format_id": "source", "ext": "mp4", "quality": 1}  # the file as uploaded: no height or codec


def picked(tmp_path, quality, formats):
    """What the real yt-dlp picks from ``formats`` with the options the client asks for. Nothing is fetched:
    it only chooses."""
    fake = FakeYDL({"requested_downloads": [{"filepath": str(tmp_path / "video.mp4")}]})
    YtDlpClient(ydl_class=fake).download_video(FILE, tmp_path, quality)
    real = yt_dlp.YoutubeDL(
        {
            "quiet": True,
            "no_warnings": True,
            "format": fake.opts["format"],
            "format_sort": fake.opts["format_sort"],
        }
    )
    listed = [{"url": f"https://example.invalid/{f['format_id']}", **f} for f in formats]
    info = {
        "id": ID_A,
        "title": "Summer drop",
        "extractor": "generic",
        "extractor_key": "Generic",
        "webpage_url": "https://example.invalid/",
        "formats": listed,
    }
    return real.process_ie_result(info, download=False)["format_id"]


@pytest.mark.parametrize("quality", ["best", "balanced", "fast"])
def test_a_drive_video_comes_down_as_the_file_that_was_uploaded(tmp_path, quality):
    assert picked(tmp_path, quality, [*COPIES, ORIGINAL]) == "source"


def test_a_drive_video_takes_googles_best_copy_when_the_original_is_stopped(tmp_path):
    assert picked(tmp_path, "best", COPIES) == "137+140"  # Google's limit drops only the original


# A campaign's footage rule


def test_drive_links_count_as_files_you_add_for_a_campaign(engine):
    campaign = add_campaign(engine)
    (project,), _ = engine.add_sources(FILE, ProjectOptions())
    takes_files = campaign.model_copy(update={"footage": Footage(files=True)})
    youtube_only = campaign.model_copy(update={"footage": Footage(videos=["arj7oStGLkU"])})
    assert footage_allows(takes_files, project) is None
    assert footage_allows(youtube_only, project) == NO_FILES.format(name=campaign.name)
