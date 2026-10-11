"""Explicit interval accounting for the currently enabled survey configuration."""
import json
import statistics


def union(intervals):
    merged=[]
    for low,high in sorted(intervals):
        if merged and low<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],high)
        else:merged.append([low,high])
    return merged


def width(intervals):return sum(high-low for low,high in union(intervals))


def accounting(db):
    rows=db.execute('SELECT region_id,start_hz,stop_hz,visit_count,valid_count,status,quality FROM inventory_regions WHERE enabled=1 ORDER BY start_hz').fetchall()
    configured=union((r[1],r[2]) for r in rows)
    valid=union((r[1],r[2]) for r in rows if r[4]>0)
    denominator=width(configured)
    missing=union((r[1],r[2]) for r in rows if not r[4])
    # Overlapping organizational profiles cannot imply missing RF coverage
    # where another enabled profile has supplied valid measurements.
    uncovered=[]
    for low,high in configured:
        cursor=low
        for a,b in valid:
            if b<=cursor or a>=high:continue
            if a>cursor:uncovered.append([cursor,min(a,high)])
            cursor=max(cursor,b)
        if cursor<high:uncovered.append([cursor,high])
    history=db.execute('SELECT m.region_id,m.observed,m.valid,r.start_hz,r.stop_hz FROM inventory_measurements m JOIN inventory_regions r USING(region_id) WHERE r.enabled=1 ORDER BY m.observed,m.measurement_id').fetchall()
    previous={};intervals=[]
    for identity,observed,okay,low,high in history:
        if identity in previous:intervals.append(observed-previous[identity])
        previous[identity]=observed
    return dict(configured_intervals_hz=configured,configured_span_hz=denominator,
        valid_intervals_hz=valid,unique_valid_hz=width(valid),valid_fraction=width(valid)/denominator if denominator else 0.,
        unmeasured_or_invalid_intervals_hz=uncovered,
        never_visited_regions=sum(r[3]==0 for r in rows),invalid_attempts=sum(not h[2] for h in history),
        attempted_sweeps=min((r[3] for r in rows),default=0),
        successful_coverage_cycles=min((r[4] for r in rows),default=0),
        valid_hz_including_revisits=sum(h[4]-h[3] for h in history if h[2]),
        average_revisit_s=statistics.mean(intervals) if intervals else None,
        failed_regions=[dict(start_hz=r[1],stop_hz=r[2],quality=json.loads(r[6] or '{}')) for r in rows if r[5]=='failed'],
        basis='Enabled configuration only. Successful cycle = minimum valid visits per segment, not simultaneous reception.',
        limitations=['Tuning support and antenna sensitivity are separate from measured coverage.',
            'No unvisited interval is silently treated as valid. Historical configuration remains in the database.'])
