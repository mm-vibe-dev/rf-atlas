"""Stable diagnostics for rejected receiver measurements; never repair RF data."""


class SpectrumMeasurementError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def rejection_code(report):
    rejected=report.get('rejected_rows',[])
    if any(row.get('bins') for row in rejected): return 'NONFINITE_OR_MALFORMED_POWER'
    if not report.get('rows'): return 'EMPTY_SPECTRUM'
    return 'MALFORMED_SPECTRUM_ROW'
