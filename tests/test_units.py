import io

import pytest
from PIL import Image

from app.imaging import ImageError, process_image
from app.messenger import host_allowed, parse_events, verify_signature
from app.models import Calc, Question
from app.screens import format_question, split_text
from app.verify import check_calcs, safe_eval
import hashlib
import hmac


# ------------------------------------------------------------------ verify
@pytest.mark.parametrize(
    "expr,expected",
    [("12*3.5", 42.0), ("3,5 × 2", 7.0), ("(1+2)**3", 27.0), ("10/4", 2.5), ("2^3", 8.0), ("1 000 + 1", 1001.0), ("-4+10", 6.0)],
)
def test_safe_eval_ok(expr, expected):
    assert safe_eval(expr) == expected


@pytest.mark.parametrize(
    "expr",
    ["__import__('os').system('id')", "open('/etc/passwd')", "a+1", "2**1000", "9**99999999", "1/0", "", "(" * 300, "[1,2]", "True+1", "'a'*3"],
)
def test_safe_eval_rejects_dangerous(expr):
    with pytest.raises((ValueError, SyntaxError, ZeroDivisionError, RecursionError)):
        safe_eval(expr)


def test_check_calcs_flags_mismatch_and_accepts_rounding():
    q = Question("1", "?", "r", calcs=[Calc("10/3", "3,33"), Calc("2+2", "5 V")])
    issues = check_calcs(q)
    assert len(issues) == 1 and "2+2 = 4" in issues[0]


def test_check_calcs_ignores_unparseable():
    q = Question("1", "?", "r", calcs=[Calc("racine(4)", "2"), Calc("2+2", "abc")])
    assert check_calcs(q) == []


# ------------------------------------------------------------------ screens
def test_split_text_never_loses_words_and_respects_limit():
    text = "Un serveur DNS traduit un nom de domaine en adresse IP. " * 20
    chunks = split_text(text, 80, first_limit=50)
    assert len(chunks[0]) <= 50 and all(len(c) <= 80 for c in chunks)
    assert " ".join(" ".join(chunks).split()) == " ".join(text.split())


def test_split_text_hard_splits_giant_words_and_empty():
    assert split_text("   ", 50) == []
    chunks = split_text("x" * 130, 50)
    assert [len(c) for c in chunks] == [50, 50, 30]


def test_format_question_short_has_source_and_flags():
    q = Question("2.a", "Définir le DNS", "Traduit un nom en IP.", source="Page 2", origin="mixte", confidence="faible", ambiguous=True)
    msgs = format_question(q, 2, 8, 450)
    assert len(msgs) == 1
    m = msgs[0]
    assert m.startswith("Q2/8 (n°2.a)") and "RÉPONSE" in m and "Page 2" in m
    assert "doc + connaissances" in m and "ambigu" in m and "incertain" in m


def test_format_question_long_answer_is_split_within_limit():
    q = Question("1", "Q" * 500, "mot " * 400, source="Page 1")
    msgs = format_question(q, 1, 3, 300, question_chars=100)
    assert len(msgs) > 3 and all(len(m) <= 300 for m in msgs)
    assert "…" in msgs[0]  # énoncé abrégé
    assert all(m.startswith("Q1/3") for m in msgs)


def test_format_question_empty_answer():
    msgs = format_question(Question("1", "?", ""), 1, 1, 450)
    assert "Informations insuffisantes" in msgs[0]


# ------------------------------------------------------------------ imaging
def test_sharp_vs_blurry(sharp_jpeg, blurry_jpeg):
    s = process_image(sharp_jpeg)
    b = process_image(blurry_jpeg)
    assert not s.is_blurry and b.is_blurry
    assert s.blur_score > 10 * max(b.blur_score, 1)


def test_resize_and_jpeg_output(sharp_jpeg):
    p = process_image(sharp_jpeg, max_side=800)
    img = Image.open(io.BytesIO(p.data))
    assert max(img.size) <= 800 and img.format == "JPEG"


def test_exif_orientation_applied():
    img = Image.new("RGB", (800, 400), "white")
    exif = img.getexif()
    exif[274] = 6  # rotation 90°
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    p = process_image(buf.getvalue(), blur_threshold=0)
    assert (p.width, p.height) == (400, 800)


def test_invalid_and_tiny_images():
    with pytest.raises(ImageError):
        process_image(b"pas une image")
    tiny = io.BytesIO()
    Image.new("RGB", (100, 100)).save(tiny, format="PNG")
    with pytest.raises(ImageError):
        process_image(tiny.getvalue())


# ------------------------------------------------------------------ messenger
def test_signature():
    body = b'{"a":1}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert verify_signature("secret", body, good)
    assert not verify_signature("secret", body, good[:-1] + "0")
    assert not verify_signature("secret", b"autre", good)
    assert not verify_signature("", body, good)  # sans secret : refus
    assert not verify_signature("secret", body, None)
    assert not verify_signature("secret", body, "md5=abc")


def test_parse_events_filters():
    payload = {
        "object": "page",
        "entry": [
            {
                "messaging": [
                    {"sender": {"id": "1"}, "message": {"mid": "m1", "text": " fin "}},
                    {"sender": {"id": "1"}, "message": {"mid": "m2", "attachments": [
                        {"type": "image", "payload": {"url": "https://x.fbcdn.net/a.jpg"}},
                        {"type": "image", "payload": {"url": "https://x.fbcdn.net/s.png", "sticker_id": 1}},
                        {"type": "video", "payload": {"url": "https://x.fbcdn.net/v.mp4"}},
                    ]}},
                    {"sender": {"id": "1"}, "message": {"mid": "m3", "text": "écho", "is_echo": True}},
                    {"sender": {"id": "1"}, "read": {"watermark": 1}},
                    {"sender": {"id": "1"}, "postback": {"payload": "X"}},
                ]
            }
        ],
    }
    evs = parse_events(payload)
    assert [(e.mid, e.text, e.image_urls) for e in evs] == [
        ("m1", "fin", []),
        ("m2", "", ["https://x.fbcdn.net/a.jpg"]),
    ]
    assert parse_events({"object": "user"}) == []


def test_host_allowed():
    suf = (".fbcdn.net", ".facebook.com")
    assert host_allowed("https://scontent.xx.fbcdn.net/v/a.jpg", suf)
    assert not host_allowed("http://scontent.xx.fbcdn.net/a.jpg", suf)  # pas https
    assert not host_allowed("https://evil.com/a.jpg", suf)
    assert not host_allowed("https://fbcdn.net.evil.com/a.jpg", suf)
    assert not host_allowed("https://169.254.169.254/latest", suf)
    assert not host_allowed("https://user:pw@scontent.fbcdn.net/a.jpg", suf)
    assert not host_allowed("not a url", suf)
