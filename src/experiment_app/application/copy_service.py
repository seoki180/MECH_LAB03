import csv
import io
from experiment_app.domain.session import ACTIVE
from experiment_app.domain.telemetry import effective_quality


class CopyService:
    def __init__(self, clock):
        self.clock = clock

    def build_tsv(self, session, snapshot, visible_channels, include_gps=True):
        if snapshot.session_id != session.session_id:
            return ""
        data = io.StringIO()
        writer = csv.writer(data, delimiter="\t", lineterminator="\n")
        writer.writerow(("session_id", "test_id", "test_revision", "part_id", "channel_id", "label",
                         "value", "unit", "sample_time_utc", "quality"))
        definition = session.snapshot.definition
        prefix = (session.session_id, definition.id, definition.revision)
        channels = {c[1]: c for c in session.snapshot.channels}
        count = 0
        for sample in snapshot.samples:
            if sample.channel_id not in visible_channels:
                continue
            part, channel, label, unit = channels[sample.channel_id]
            writer.writerow((*prefix, part, channel, label, "" if sample.value is None else repr(sample.value),
                             unit, sample.source_time_utc or sample.received_time_utc,
                             effective_quality(sample, self.clock.monotonic(), session.state in ACTIVE)))
            count += 1
        if snapshot.gps and include_gps:
            gps = snapshot.gps
            for channel, label, value in (("latitude", "위도", gps.latitude), ("longitude", "경도", gps.longitude)):
                writer.writerow((*prefix, "gps", channel, label, "" if value is None else repr(value),
                                 "deg", gps.received_time_utc, gps.fix_quality))
                count += 1
        return data.getvalue() if count else ""
