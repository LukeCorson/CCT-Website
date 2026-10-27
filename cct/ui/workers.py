"""Read-only history work never blocks acquisition or the operator interface."""

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class WorkerSignals(QObject):
    result = Signal(int, object)
    error = Signal(int, str)
    finished = Signal(int)


class HistoryWorker(QRunnable):
    def __init__(self, key, operation, *args):
        super().__init__()
        self.operation, self.args = operation, args
        self.key = key
        self.signals = WorkerSignals()

    @Slot()
    def run(self):
        try:
            self.signals.result.emit(self.key, self.operation(*self.args))
        except Exception as exc:
            self.signals.error.emit(self.key, str(exc))
        finally:
            self.signals.finished.emit(self.key)


def bounded_rows(rows, limit=20000):
    """Keep local extrema across signals rather than hiding narrow peaks.

    Stored measurements and CSV exports remain untouched. One row carries all
    channels, so choosing a row also preserves alignment between signals.
    """
    if len(rows) <= limit:
        return rows
    channels = ("co2_ppm", "flow_sccm", "column_temp_c", "breakthrough_ratio")
    # Each bucket contributes at most two extrema per channel plus its edges.
    bucket_count = max(1, limit // (2 * len(channels) + 3))
    bucket_size = max(1, (len(rows) + bucket_count - 1) // bucket_count)
    selected = set()
    for start in range(0, len(rows), bucket_size):
        end = min(start + bucket_size, len(rows))
        selected.update((start, end - 1))
        for key in channels:
            candidates = [(idx, rows[idx].get(key)) for idx in range(start, end) if isinstance(rows[idx].get(key), (int, float))]
            if candidates:
                selected.add(min(candidates, key=lambda item: item[1])[0])
                selected.add(max(candidates, key=lambda item: item[1])[0])
        # Preserve a representative missing-data gap within this bucket.
        missing = next((idx for idx in range(start, end) if rows[idx].get("co2_ppm") is None), None)
        if missing is not None:
            selected.add(missing)
    return [rows[idx] for idx in sorted(selected)]


def load_for_display(service, run_id):
    run = service.load_run(run_id)
    samples = run.get("samples") or []
    run["_sample_count"] = len(samples)
    run["samples"] = bounded_rows(samples)
    return run
