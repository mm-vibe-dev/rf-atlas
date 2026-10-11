"""Explicit new receiver-clock conversion; ambiguous legacy values stay raw."""
from datetime import datetime, timezone


def aware_utc(value):
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
    except (ValueError,TypeError,AttributeError,OverflowError):
        return None


def receiver_utc(value, acquisition_started=None):
    normalized=aware_utc(value)
    if normalized:return normalized,'EXPLICIT_OFFSET'
    parsed=datetime.fromisoformat(value)
    candidates=[parsed.replace(fold=fold).astimezone(timezone.utc) for fold in (0,1)]
    reference=aware_utc(acquisition_started)
    if reference:
        started=datetime.fromisoformat(reference)
        selected=min(candidates,key=lambda d:abs((d-started).total_seconds()))
        if abs((selected-started).total_seconds())>360:
            raise ValueError('Receiver clock is inconsistent with acquisition start')
    else:selected=candidates[0]
    return selected.isoformat(),'RECEIVER_HOST_LOCAL_CLOCK'


def sql_utc(column):
    """Only explicitly offset legacy values may participate in UTC filters."""
    return f"(CASE WHEN upper(substr({column},-1))='Z' OR instr(substr({column},11),'+')>0 OR instr(substr({column},11),'-')>0 THEN {column} ELSE NULL END)"


def observation_utc(alias='o'):
    return f'COALESCE({alias}.observed_utc,{sql_utc(alias+".observed")})'
