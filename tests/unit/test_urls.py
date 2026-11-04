from urllib.parse import quote

import pytest

from clipforge.ingest.urls import (
    DRIVE_NOT_A_FILE,
    VIDEO_EXTS,
    Rejected,
    SourceRef,
    canonical_url,
    drive_file_url,
    drive_folder_url,
    is_drive,
    parse_inputs,
    parse_timestamp,
    video_title,
    youtube_id,
)

TABLE = [
    ("https://www.youtube.com/watch?v=arj7oStGLkU", "arj7oStGLkU", None),
    ("https://youtu.be/arj7oStGLkU?t=90", "arj7oStGLkU", 90),
    ("https://www.youtube.com/watch?v=arj7oStGLkU&t=1m30s", "arj7oStGLkU", 90),
    ("https://youtube.com/shorts/abcdefghijk", "abcdefghijk", None),
    ("https://www.youtube.com/live/abcdefghijk?si=xyz", "abcdefghijk", None),
    ("https://www.youtube.com/embed/abcdefghijk?start=42", "abcdefghijk", 42),
    ("https://m.youtube.com/watch?v=abcdefghijk&feature=share", "abcdefghijk", None),
    ("https://music.youtube.com/watch?v=abcdefghijk&list=RDAMVM", "abcdefghijk", None),
    ("www.youtube.com/watch?v=abcdefghijk", "abcdefghijk", None),
    ("https://www.youtube.com/watch?app=desktop&v=abcdefghijk", "abcdefghijk", None),
    ("https://www.youtube-nocookie.com/embed/abcdefghijk", "abcdefghijk", None),
    ("https://youtu.be/abcdefghijk#t=1h2m3s", "abcdefghijk", 3723),
    ("https://youtu.be/abcdefghijk?si=AbC123xyz", "abcdefghijk", None),
    ("https://youtu.be/abcdefghijk?t=0", "abcdefghijk", None),  # t=0 is the start, not a moment
    ("youtube.com/watch?v=abcdefghijk", "abcdefghijk", None),
    ("http://www.youtube.com/v/abcdefghijk", "abcdefghijk", None),
    ("<https://youtu.be/abcdefghijk>.", "abcdefghijk", None),
]


@pytest.mark.parametrize("text,video_id,focus", TABLE)
def test_parse_inputs_table(text, video_id, focus):
    refs, rejected = parse_inputs(text)
    assert rejected == []
    assert refs == [SourceRef("youtube", canonical_url(video_id), video_id, None, focus)]


@pytest.mark.parametrize(
    "value,expected",
    [
        ("90", 90),
        ("90s", 90),
        ("1m30s", 90),
        ("1h2m3s", 3723),
        ("1:30", 90),
        ("01:02:03", 3723),
        ("1m", 60),
        ("abc", None),
        ("", None),
    ],
)
def test_parse_timestamp(value, expected):
    assert parse_timestamp(value) == expected


def test_youtube_id_and_canonical_url():
    assert youtube_id("https://youtu.be/arj7oStGLkU?t=5") == "arj7oStGLkU"
    assert youtube_id("www.youtube.com/watch?v=arj7oStGLkU") == "arj7oStGLkU"
    assert youtube_id("https://youtu.be/short") is None
    assert youtube_id("https://vimeo.com/123456") is None
    assert canonical_url("arj7oStGLkU") == "https://www.youtube.com/watch?v=arj7oStGLkU"
    assert {".mp4", ".mov", ".mkv", ".webm", ".m4v"} == VIDEO_EXTS


def test_playlist_only_rejected():
    text = "https://www.youtube.com/playlist?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG"
    refs, rejected = parse_inputs(text)
    assert refs == [] and len(rejected) == 1
    assert rejected[0].token == text and "single video link" in rejected[0].reason


@pytest.mark.parametrize(
    "text",
    [
        "https://www.youtube.com/@somechannel",
        "https://youtu.be/short",
        "https://www.youtube.com/results?search_query=cats",
    ],
)
def test_youtube_link_without_a_video_rejected(text):
    refs, rejected = parse_inputs(text)
    assert refs == [] and "single video link" in rejected[0].reason


def test_other_site_rejected_unless_allowed():
    refs, rejected = parse_inputs("https://vimeo.com/123456")
    assert refs == [] and "Allow other sites" in rejected[0].reason
    refs, rejected = parse_inputs("https://vimeo.com/123456", allow_other_sites=True)
    assert rejected == [] and refs == [SourceRef("other", "https://vimeo.com/123456", None, None, None)]


def test_only_web_links_count_as_other_sites():
    refs, rejected = parse_inputs("ftp://example.com/talk.mp4", allow_other_sites=True)
    assert refs == [] and len(rejected) == 1


def test_local_files(tmp_path):
    video = tmp_path / "talk.mp4"
    video.write_bytes(b"x")
    spaced = tmp_path / "My Video.mov"
    spaced.write_bytes(b"x")
    notes = tmp_path / "notes.txt"
    notes.write_text("hi")
    folder = tmp_path / "folder"
    folder.mkdir()
    missing = tmp_path / "gone.mp4"
    text = "\n".join([str(video), "file://" + quote(str(spaced)), str(missing), str(notes), str(folder)])
    refs, rejected = parse_inputs(text)
    assert refs == [
        SourceRef("file", None, None, video.resolve(), None),
        SourceRef("file", None, None, spaced.resolve(), None),
    ]
    assert [r.token for r in rejected] == [str(missing), str(notes), str(folder)]
    assert rejected[0].reason.startswith("File not found")
    assert rejected[1].reason.startswith("Not a video file")
    assert "folder" in rejected[2].reason
    assert all(isinstance(r, Rejected) for r in rejected)


def test_paths_with_spaces(tmp_path):
    spaced = tmp_path / "My Video.mov"
    spaced.write_bytes(b"x")
    escaped = str(spaced).replace(" ", "\\ ")  # how Terminal pastes a dragged file
    for text in (
        str(spaced),
        f"'{spaced}'",
        f'"{spaced}"',
        escaped,
        f"{escaped} https://youtu.be/abcdefghijk",
    ):
        refs, rejected = parse_inputs(text)
        assert rejected == [], text
        assert refs[0] == SourceRef("file", None, None, spaced.resolve(), None), text


def test_duplicates_removed(tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    text = (
        "https://youtu.be/arj7oStGLkU?t=30, https://www.youtube.com/watch?v=arj7oStGLkU\n"
        f"{video} {tmp_path}/./a.mp4 https://youtu.be/abcdefghijk https://youtu.be/abcdefghijk"
    )
    refs, rejected = parse_inputs(text)
    assert rejected == []
    assert [(r.kind, r.video_id, r.focus_s) for r in refs] == [
        ("youtube", "arj7oStGLkU", 30),
        ("file", None, None),
        ("youtube", "abcdefghijk", None),
    ]


def test_words_around_links_are_ignored():
    refs, rejected = parse_inputs("Clip this one please: https://youtu.be/abcdefghijk, it's great.")
    assert [r.video_id for r in refs] == ["abcdefghijk"] and rejected == []


# Google Drive links (SPEC 7.1)

FILE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
FOLDER_ID = "1FoLdErIdAbCdEfGhIjKlMnOpQrStUvWx"


@pytest.mark.parametrize(
    "text",
    [
        f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing",
        f"https://drive.google.com/file/d/{FILE_ID}/view?usp=drive_link",
        f"https://drive.google.com/file/d/{FILE_ID}",
        f"https://drive.google.com/open?id={FILE_ID}",
        f"https://drive.google.com/uc?id={FILE_ID}&export=download",
        f"https://drive.google.com/uc?export=download&id={FILE_ID}",
        f"https://drive.usercontent.google.com/download?id={FILE_ID}&export=download",
        f"https://docs.google.com/file/d/{FILE_ID}/edit",
        f"drive.google.com/file/d/{FILE_ID}/view",
        f"<https://drive.google.com/file/d/{FILE_ID}/view>.",
    ],
)
@pytest.mark.parametrize("allow_other_sites", [False, True])
def test_drive_file_links_are_drive_sources(text, allow_other_sites):
    refs, rejected = parse_inputs(text, allow_other_sites=allow_other_sites)
    assert rejected == []
    assert refs == [SourceRef("drive", drive_file_url(FILE_ID), FILE_ID, None, None)]
    assert drive_file_url(FILE_ID) == f"https://drive.google.com/file/d/{FILE_ID}/view"


@pytest.mark.parametrize(
    "text",
    [
        f"https://drive.google.com/drive/folders/{FOLDER_ID}?usp=sharing",
        f"https://drive.google.com/drive/folders/{FOLDER_ID}",
        f"https://drive.google.com/drive/u/0/folders/{FOLDER_ID}",
        f"https://docs.google.com/drive/folders/{FOLDER_ID}",
    ],
)
def test_drive_folder_links_are_drive_folders(text):
    refs, rejected = parse_inputs(text)
    assert rejected == []
    assert refs == [SourceRef("drive_folder", drive_folder_url(FOLDER_ID), FOLDER_ID, None, None)]
    assert drive_folder_url(FOLDER_ID) == f"https://drive.google.com/drive/folders/{FOLDER_ID}"


@pytest.mark.parametrize(
    "text",
    [
        "https://drive.google.com/drive/my-drive",
        "https://drive.google.com/file/d/tooshort/view",
        f"https://docs.google.com/document/d/{FILE_ID}/edit",
        "https://drive.google.com/open?id=",
    ],
)
def test_drive_link_to_no_file_or_folder_is_rejected(text):
    refs, rejected = parse_inputs(text, allow_other_sites=True)
    assert refs == [] and rejected == [Rejected(text, DRIVE_NOT_A_FILE)]


def test_drive_duplicates_removed():
    text = f"https://drive.google.com/open?id={FILE_ID} https://drive.google.com/file/d/{FILE_ID}/view"
    refs, _ = parse_inputs(text)
    assert [r.video_id for r in refs] == [FILE_ID]


def test_is_drive_takes_only_drive_hosts():
    assert is_drive(drive_file_url(FILE_ID)) and is_drive(drive_folder_url(FOLDER_ID))
    assert is_drive(f"https://drive.usercontent.google.com/download?id={FILE_ID}")
    assert not is_drive("https://www.youtube.com/watch?v=arj7oStGLkU")
    assert not is_drive(f"https://drive.google.com.example.net/file/d/{FILE_ID}/view")
    assert not is_drive(f"https://example.net/drive.google.com/file/d/{FILE_ID}/view")


def test_video_title_drops_a_video_extension():
    assert video_title("Summer drop.mp4") == "Summer drop"
    assert video_title("Summer drop.MOV") == "Summer drop"
    assert video_title("v1.2 final cut") == "v1.2 final cut"
    assert video_title("Brief.pdf") == "Brief.pdf"
