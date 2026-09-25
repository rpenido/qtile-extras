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
from typing import Any

from libqtile.log_utils import logger
from libqtile.utils import acall_process
from libqtile.widget import base

# Default Nerd Font glyphs. Override via the `tracking_icon` and
# `total_icon` config options.
DEFAULT_TRACKING_ICON = "󰔛"
DEFAULT_TOTAL_ICON = ""

# Timewarrior only quotes a tag when it contains a character outside this set.
# The set was determined empirically from `timew` output. Note that this
# excludes '-', '_' and '/', all of which Timewarrior does quote.
TAG_SAFE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
TAG_SAFE_PUNCTUATION = frozenset("#$&'*,.:;>?@[\\]`{|}")


def format_duration(seconds: float) -> str:
    """
    Format a duration the way the Timewarrior CLI reports it.

    Durations are rendered as ``H:MM:SS`` with the hours accumulating, so an
    interval lasting 33 hours 14 minutes and 42 seconds is displayed as
    ``33:14:42``. Negative values are clamped to zero.

    :param seconds: Duration in seconds.
    """
    total = int(max(seconds, 0))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)

    return f"{hours}:{minutes:02d}:{secs:02d}"


def quote_tag(tag: str) -> str:
    """
    Render a tag the way the Timewarrior CLI renders it.

    Tags are only quoted when they contain a character that Timewarrior
    considers unsafe. Inside quotes, double quotes are backslash escaped.

    :param tag: The tag to render.
    """
    if all(char in TAG_SAFE_CHARS or char in TAG_SAFE_PUNCTUATION for char in tag):
        return tag

    return '"{}"'.format(tag.replace('"', '\\"'))


def format_tags(tags: list[str]) -> str:
    """
    Render a list of tags as a single space separated string.

    :param tags: Tags as reported by Timewarrior.
    """
    return " ".join(quote_tag(tag) for tag in tags)


def day_bounds(now: datetime) -> tuple[datetime, datetime]:
    """
    Return the start and end of the local day containing ``now``.

    :param now: A timezone aware datetime.
    """
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    return start, start + timedelta(days=1)


def parse_timew_timestamp(value: str) -> datetime:
    """
    Parse a timestamp from Timewarrior's JSON export.

    Timewarrior exports timestamps in the form ``YYYYMMDDTHHMMSSZ`` which
    are always UTC, so a timezone aware datetime in UTC is returned.

    :param value: The timestamp string.
    """
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def tracked_today(intervals: list[dict], now: datetime) -> float:
    """
    Return the number of seconds tracked during the current local day.

    Each interval is clipped to the day boundaries before it is added to the
    total. This means an interval that starts before midnight only contributes
    the part of it that falls on the current day. Intervals that are still
    running are measured up to ``now`` and nothing is ever counted beyond the
    current time.

    Overlapping intervals are summed independently, so a set of overlapping
    intervals may report more than 24 hours.

    :param intervals: Interval dictionaries as reported by `timew export`.
    :param now: The current local time.
    """
    start_of_day, end_of_day = day_bounds(now)
    total = 0.0

    for interval in intervals:
        start = parse_timew_timestamp(interval["start"])

        if "end" in interval:
            end = parse_timew_timestamp(interval["end"])
        else:
            # The interval is still being tracked so it runs until now.
            end = now

        # Only count the portion of the interval that falls within today and
        # never count time that has not happened yet.
        start = max(start, start_of_day)
        end = min(end, end_of_day, now)

        if end > start:
            total += (end - start).total_seconds()

    return total


def get_active_interval(intervals: list[dict]) -> dict | None:
    """
    Return the currently tracked interval, if there is one.

    Timewarrior marks a running interval by omitting its ``end`` key.

    :param intervals: Interval dictionaries as reported by `timew export`.
    """
    for interval in intervals:
        if "end" not in interval:
            return interval

    return None


def render_status(
    intervals: list[dict],
    now: datetime,
    tracking_icon: str = DEFAULT_TRACKING_ICON,
    total_icon: str = DEFAULT_TOTAL_ICON,
) -> str:
    """
    Build the status string displayed by the widget.

    When an interval is being tracked the output is
    ``<tracking_icon> <tags> · <total> <total_icon> <tracked>``. Otherwise only
    ``<total_icon> <tracked>`` is returned.

    :param intervals: Interval dictionaries as reported by `timew export`.
    :param now: The current local time.
    :param tracking_icon: Icon shown when an interval is being tracked.
    :param total_icon: Icon shown before the time tracked today.
    """
    parts = []
    active = get_active_interval(intervals)

    if active is not None:
        tags = format_tags(active.get("tags", [])) or "?"
        elapsed = format_duration((now - parse_timew_timestamp(active["start"])).total_seconds())
        parts.append(f"{tracking_icon} {tags} · {elapsed}")

    parts.append(f"{total_icon} {format_duration(tracked_today(intervals, now))}")

    return " ".join(parts)


class Timewarrior(base.BackgroundPoll):
    """
    A widget to display Timewarrior time tracking status.

    The widget shows the time tracked so far today and, when an interval is
    being tracked, the tags of the active interval alongside the time elapsed
    on it.

    Intervals are read using ``timew export`` so that no parsing of the
    human readable reports is required. Only the time spent within the current
    day is counted, so an interval that was started on a previous day will not
    inflate today's total.

    The icons can be changed using the ``tracking_icon`` and ``total_icon``
    options. Both accept any text, so they can be set to an empty string to
    hide them.

    Requires the ``timew`` binary from Timewarrior to be installed and
    available on the path.
    """

    orientations = base.ORIENTATION_HORIZONTAL
    defaults: list[tuple[str, Any, str]] = [
        ("update_interval", 1, "Time in seconds between updates."),
        ("timew_cmd", "timew", "Path to the timew binary."),
        ("tracking_icon", DEFAULT_TRACKING_ICON, "Icon shown when tracking is active."),
        ("total_icon", DEFAULT_TOTAL_ICON, "Icon shown before the time tracked today."),
    ]

    def __init__(self, **config):
        base.BackgroundPoll.__init__(self, "", **config)
        self.add_defaults(Timewarrior.defaults)
        self._error_logged = False
        self.tracking = False
        self.tags: list[str] = []
        self.tracked = 0.0
        self.status = ""

        self.add_callbacks({"Button1": self.force_update})

    async def apoll(self) -> str:
        """Fetch the current Timewarrior status and return the text to display."""
        command = [self.timew_cmd, "export", ":today"]

        try:
            raw = await acall_process(command)
        except FileNotFoundError:
            if not self._error_logged:
                logger.warning("Could not find '%s'. Is Timewarrior installed?", self.timew_cmd)
                self._error_logged = True
            return self.status

        try:
            intervals = json.loads(raw)
        except json.JSONDecodeError:
            if not self._error_logged:
                logger.warning("Could not parse output from '%s'.", self.timew_cmd)
                self._error_logged = True
            return self.status

        now = datetime.now().astimezone()
        active = get_active_interval(intervals)

        self._error_logged = False
        self.tracking = active is not None
        self.tags = list(active.get("tags", [])) if active else []
        self.tracked = tracked_today(intervals, now)

        self.status = render_status(intervals, now, self.tracking_icon, self.total_icon)

        return self.status

    def info(self):
        info = base.BackgroundPoll.info(self)
        info["tracking"] = self.tracking
        info["tags"] = self.tags
        info["tracked"] = self.tracked

        return info
