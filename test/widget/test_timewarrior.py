# Copyright (c) 2026 rpenido
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
import json
from datetime import datetime, timedelta, timezone

import libqtile.bar
import libqtile.config
import libqtile.confreader
import libqtile.layout
import pytest
from libqtile.resources import default_config

from qtile_extras.widget import timewarrior
from qtile_extras.widget.timewarrior import (
    Timewarrior,
    format_duration,
    format_tags,
    get_active_interval,
    parse_timew_timestamp,
    quote_tag,
    render_status,
    tracked_today,
)
from test.helpers import Retry  # noqa: I001

# The machine running the reference probes was in UTC-3.
TZ = timezone(timedelta(hours=-3))


def local(*args):
    """Build a timezone aware datetime in the reference timezone."""
    return datetime(*args, tzinfo=TZ)


def utc(*args):
    """Build a timezone aware datetime in UTC."""
    return datetime(*args, tzinfo=timezone.utc)


# Raw payloads captured from `timew export :today` on timew 1.9.1.
EXPORT_EMPTY = "[\n]\n"

# Two intervals: one overnight (22:00 local -> 02:00 local) and one during
# the day (09:00 local -> 10:00 local). `timew day` reported "Tracked 3:00:00".
EXPORT_OVERNIGHT_AND_DAY = """[
{"id":2,"start":"20260924T010000Z","end":"20260925T050000Z","tags":["Overnight"]},
{"id":1,"start":"20260925T120000Z","end":"20260925T130000Z","tags":["DayTag"]}
]"""

# An open interval that started the previous day. `timew day` reported
# "Tracked 10:14:42" at a local time of 10:14:42.
EXPORT_OPEN_FROM_YESTERDAY = """[
{"id":1,"start":"20260924T040000Z","tags":["SpanTag"]}
]"""


@pytest.fixture
def twdb(monkeypatch):
    """Patch `acall_process` so no real timew process or database is used.

    The optional `call_log` path is appended to on every call. This is needed
    by tests that check the widget polled again, because the widget runs in a
    forked process and so cannot share state with the test process.
    """

    def factory(payload=EXPORT_EMPTY, error=None, call_log=None):
        async def mock_acall_process(command, shell=False):
            if call_log is not None:
                with open(call_log, "a") as log:
                    log.write("call\n")
            if error is not None:
                raise error
            return payload

        monkeypatch.setattr(timewarrior, "acall_process", mock_acall_process)

    return factory


@pytest.fixture
def frozen_time(monkeypatch):
    """Freeze the clock used by the widget.

    The widget reads the current time itself, so the clock is pinned to match
    the reference outputs captured from `timew`.
    """
    fixed = local(2026, 9, 25, 10, 14, 42)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed

    monkeypatch.setattr(timewarrior, "datetime", FrozenDatetime)


@pytest.fixture
def timewarriorconfig(request):
    """Config for the Timewarrior widget. Parameters set via request."""

    class TimewarriorConfig(libqtile.confreader.Config):
        auto_fullscreen = True
        keys = []
        mouse = []
        groups = [libqtile.config.Group("a")]
        layouts = [libqtile.layout.Max()]
        floating_layout = default_config.floating_layout
        screens = [
            libqtile.config.Screen(
                top=libqtile.bar.Bar(
                    [Timewarrior(**getattr(request, "param", dict()))],
                    50,
                ),
            )
        ]

    yield TimewarriorConfig


#
# format_duration
#


@pytest.mark.parametrize(
    "seconds,expected",
    [
        # All expected values are taken from real `timew` output.
        (0, "0:00:00"),
        (15, "0:00:15"),
        (2 * 3600 + 14 * 60 + 2, "2:14:02"),
        (10 * 3600 + 14 * 60 + 42, "10:14:42"),
        (24 * 3600, "24:00:00"),
        # Timewarrior renders a 33 hour interval as "33:14:42" with no day
        # prefix, so hours simply accumulate.
        (33 * 3600 + 14 * 60 + 42, "33:14:42"),
        (59, "0:00:59"),
        (3600, "1:00:00"),
        (-10, "0:00:00"),
    ],
)
def test_format_duration(seconds, expected):
    assert format_duration(seconds) == expected


#
# quote_tag / format_tags
#


@pytest.mark.parametrize(
    "tag,expected",
    [
        # Timewarrior leaves these unquoted.
        ("plain", "plain"),
        ("MultiWord", "MultiWord"),
        ("a#b", "a#b"),
        ("a$b", "a$b"),
        ("a&b", "a&b"),
        ("a'b", "a'b"),
        ("a*b", "a*b"),
        ("a,b", "a,b"),
        ("a.b", "a.b"),
        ("a:b", "a:b"),
        ("a;b", "a;b"),
        ("a>b", "a>b"),
        ("a?b", "a?b"),
        ("a@b", "a@b"),
        ("a[b", "a[b"),
        ("a\\b", "a\\b"),
        ("a]b", "a]b"),
        ("a`b", "a`b"),
        ("a{b", "a{b"),
        ("a|b", "a|b"),
        ("a}b", "a}b"),
        # Real `timew` output quotes this: Tracking "FAL_3539"
        ("FAL_3539", '"FAL_3539"'),
        # Timewarrior quotes these.
        ("has space", '"has space"'),
        ('has"quote', '"has\\"quote"'),
        ("a-b", '"a-b"'),
        ("a_b", '"a_b"'),
        ("a/b", '"a/b"'),
        ("a+b", '"a+b"'),
        ("a=b", '"a=b"'),
        ("a%b", '"a%b"'),
        ("a(b", '"a(b"'),
        ("a)b", '"a)b"'),
        ("a<b", '"a<b"'),
        ("a^b", '"a^b"'),
        ("a~b", '"a~b"'),
        ("a!b", '"a!b"'),
        # Timewarrior cannot store an empty tag so this is not a case that
        # occurs in practice. An empty tag renders as an empty string, which
        # `render_status` treats as missing and replaces with "?".
        ("", ""),
    ],
)
def test_quote_tag(tag, expected):
    assert quote_tag(tag) == expected


def test_format_tags():
    # `timew` renders `timew start "Multi Word Tag" Simple` as
    # Tracking "Multi Word Tag" Simple
    assert format_tags(["Multi Word Tag", "Simple"]) == '"Multi Word Tag" Simple'
    assert format_tags([]) == ""


#
# parse_timew_timestamp
#


def test_parse_timew_timestamp_is_utc():
    parsed = parse_timew_timestamp("20260925T130235Z")
    assert parsed == utc(2026, 9, 25, 13, 2, 35)
    assert parsed.tzinfo is timezone.utc


#
# tracked_today
#


def test_tracked_today_overnight_only_counts_current_day():
    """An interval spanning midnight only contributes today's portion."""
    # 22:00 local yesterday -> 02:00 local today.
    intervals = json.loads(
        '[{"id":1,"start":"20260924T010000Z","end":"20260925T050000Z","tags":["Overnight"]}]'
    )
    now = local(2026, 9, 25, 10, 14, 2)

    # 00:00 -> 02:00 local today is two hours.
    assert tracked_today(intervals, now) == 2 * 3600


def test_tracked_today_known_answer():
    """Known-answer test against real `timew day` output."""
    intervals = json.loads(EXPORT_OVERNIGHT_AND_DAY)
    now = local(2026, 9, 25, 10, 14, 2)

    # `timew day` reported "Tracked 3:00:00".
    assert format_duration(tracked_today(intervals, now)) == "3:00:00"


def test_tracked_today_open_interval_from_yesterday():
    """A running interval only counts the time elapsed since midnight."""
    intervals = json.loads(EXPORT_OPEN_FROM_YESTERDAY)
    now = local(2026, 9, 25, 10, 14, 42)

    # `timew day` reported "Tracked 10:14:42" even though the interval itself
    # had been running for 33 hours.
    assert format_duration(tracked_today(intervals, now)) == "10:14:42"


def test_tracked_today_ignores_interval_from_previous_day():
    intervals = json.loads(
        '[{"start":"20260924T130000Z","end":"20260924T140000Z","tags":["Yesterday"]}]'
    )
    assert tracked_today(intervals, local(2026, 9, 25, 10, 0)) == 0


def test_tracked_today_ignores_future_interval():
    """Time that has not happened yet is never counted."""
    intervals = json.loads(
        '[{"start":"20260925T140000Z","end":"20260925T150000Z","tags":["Later"]}]'
    )
    assert tracked_today(intervals, local(2026, 9, 25, 10, 0)) == 0


def test_tracked_today_empty_database():
    assert tracked_today([], local(2026, 9, 25, 10, 0)) == 0


def test_tracked_today_sums_multiple_intervals():
    intervals = json.loads(
        '[{"start":"20260925T030000Z","end":"20260925T040000Z","tags":["A"]},'
        ' {"start":"20260925T050000Z","end":"20260925T060000Z","tags":["B"]}]'
    )
    assert tracked_today(intervals, local(2026, 9, 25, 10, 0)) == 2 * 3600


def test_tracked_today_uses_local_day_not_utc_day():
    """The day boundary follows the local timezone, not UTC.

    00:30 local on UTC-3 is 03:30 UTC, so an interval recorded entirely
    within UTC's day but before local midnight must not be counted.
    """
    intervals = json.loads(
        '[{"start":"20260925T010000Z","end":"20260925T013000Z","tags":["BeforeMidnight"]}]'
    )
    # 01:00Z -> 01:30Z is 22:00 -> 22:30 local on the previous day.
    assert tracked_today(intervals, local(2026, 9, 25, 10, 0)) == 0


#
# get_active_interval
#


def test_get_active_interval():
    running = {"id": 1, "start": "20260925T131432Z", "tags": ["Work"]}
    closed = {"id": 2, "start": "20260925T130000Z", "end": "20260925T140000Z"}

    assert get_active_interval([closed, running]) == running
    assert get_active_interval([closed]) is None
    assert get_active_interval([]) is None


#
# render_status
#


def test_render_status_while_tracking():
    intervals = json.loads(EXPORT_OPEN_FROM_YESTERDAY)
    now = local(2026, 9, 25, 10, 14, 42)

    assert render_status(intervals, now) == "󰔛 SpanTag · 33:14:42  10:14:42"


def test_render_status_not_tracking():
    intervals = json.loads(EXPORT_OVERNIGHT_AND_DAY)
    now = local(2026, 9, 25, 10, 14, 2)

    assert render_status(intervals, now) == " 3:00:00"


def test_render_status_empty_database():
    assert render_status([], local(2026, 9, 25, 10, 0)) == " 0:00:00"


def test_render_status_custom_icons():
    """Icons can be overridden, including removing them entirely."""
    intervals = json.loads(EXPORT_OPEN_FROM_YESTERDAY)
    now = local(2026, 9, 25, 10, 14, 42)

    assert render_status(intervals, now, "[", "]") == "[ SpanTag · 33:14:42 ] 10:14:42"
    assert render_status(intervals, now, "", "") == " SpanTag · 33:14:42  10:14:42"


def test_render_status_untagged_interval():
    """An interval with no tags falls back to a question mark."""
    intervals = json.loads('[{"start":"20260925T131432Z","tags":[]}]')
    now = local(2026, 9, 25, 10, 14, 42)

    assert render_status(intervals, now).startswith("󰔛 ? ·")


#
# Widget tests
#


@Retry(ignore_exceptions=(AssertionError,))
def status_is(manager, expected):
    """Waits for the widget to report the expected status."""
    assert manager.c.widget["timewarrior"].eval("self.status") == expected


def test_timewarrior_tracking(twdb, frozen_time, manager_nospawn, timewarriorconfig):
    twdb(EXPORT_OPEN_FROM_YESTERDAY)
    manager_nospawn.start(timewarriorconfig)
    status_is(manager_nospawn, "󰔛 SpanTag · 33:14:42  10:14:42")

    widget = manager_nospawn.c.widget["timewarrior"]
    assert widget.info()["tracking"] is True
    assert widget.info()["tags"] == ["SpanTag"]


def test_timewarrior_not_tracking(twdb, frozen_time, manager_nospawn, timewarriorconfig):
    twdb(EXPORT_OVERNIGHT_AND_DAY)
    manager_nospawn.start(timewarriorconfig)
    status_is(manager_nospawn, " 3:00:00")

    widget = manager_nospawn.c.widget["timewarrior"]
    assert widget.info()["tracking"] is False
    assert widget.info()["tags"] == []


def test_timewarrior_empty_database(twdb, frozen_time, manager_nospawn, timewarriorconfig):
    twdb(EXPORT_EMPTY)
    manager_nospawn.start(timewarriorconfig)
    status_is(manager_nospawn, " 0:00:00")

    assert manager_nospawn.c.widget["timewarrior"].info()["tracked"] == 0


@pytest.mark.parametrize(
    "timewarriorconfig", [{"tracking_icon": "[", "total_icon": "]"}], indirect=True
)
def test_timewarrior_custom_icons(twdb, frozen_time, manager_nospawn, timewarriorconfig):
    twdb(EXPORT_OPEN_FROM_YESTERDAY)
    manager_nospawn.start(timewarriorconfig)
    status_is(manager_nospawn, "[ SpanTag · 33:14:42 ] 10:14:42")


def test_timewarrior_missing_binary(twdb, manager_nospawn, timewarriorconfig):
    """A missing timew binary is logged once and the widget keeps its text."""
    twdb(error=FileNotFoundError())
    manager_nospawn.start(timewarriorconfig)

    assert manager_nospawn.c.widget["timewarrior"].eval("self.status") == ""


def test_timewarrior_bad_output(twdb, manager_nospawn, timewarriorconfig):
    """Unparsable output is handled without raising."""
    twdb("this is not json")
    manager_nospawn.start(timewarriorconfig)

    assert manager_nospawn.c.widget["timewarrior"].eval("self.status") == ""


def test_timewarrior_click_updates(
    twdb, frozen_time, manager_nospawn, timewarriorconfig, tmp_path
):
    """Clicking the widget triggers an extra poll of timew."""
    logfile = tmp_path / "timew-calls.log"
    twdb(EXPORT_EMPTY, call_log=logfile)
    manager_nospawn.start(timewarriorconfig)
    status_is(manager_nospawn, " 0:00:00")

    # Count the polls made so far, then click and wait for another one.
    before = len(logfile.read_text().splitlines())
    manager_nospawn.c.bar["top"].fake_button_press(0, 0, 1)

    @Retry(ignore_exceptions=(AssertionError,), fail_msg="Widget did not poll after click.")
    def polled_again():
        assert len(logfile.read_text().splitlines()) > before

    polled_again()
