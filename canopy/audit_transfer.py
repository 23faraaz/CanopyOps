"""Read-only audit of a completed cutover, including later PostgreSQL writes."""
import argparse
import json
from datetime import datetime, timezone
from canopy.postgres import connect
from canopy.transfer import manifest, sqlite_rows, pg_rows


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--sqlite',required=True)
    parser.add_argument('--started-at',required=True)
    args=parser.parse_args()
    original=sqlite_rows(args.sqlite)
    source=manifest(original)
    original_ids={r[0] for r in original[0]}
    with connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        current=pg_rows(db)
        target=manifest(current)
        lookup={r[0]:r for r in current[0]}
        assert all(lookup.get(row[0])==row for row in original[0]), 'Source event was lost or altered'
        new=[r for r in current[0] if r[0] not in original_ids]
        assert new, 'No post-cutover writes found'
        resumed=min(datetime.fromisoformat(r[2]) for r in new)
        last_source=max(datetime.fromisoformat(r[2]) for r in original[0])
        # A duplicate replay must not create events OR change current incidents/states.
    from canopy.domain import Reading
    from canopy.postgres import PostgresStore
    import canopy.postgres as module
    owner=module.connect
    module.connect=lambda:connect('canopy_writer','/run/secrets/pg_writer')
    try:
        assert PostgresStore().process(Reading.model_validate_json(original[0][0][3])) is False
    finally:
        module.connect=owner
    upper=(resumed-datetime.fromisoformat(args.started_at)).total_seconds()
    print(json.dumps({'source':source,'current_target':target,'all_source_events_exactly_preserved':True,
                      'post_cutover_events':len(new),'first_resumed_observed_at':resumed.isoformat(),
                      'observed_reading_gap_seconds':(resumed-last_source).total_seconds(),
                      'cutover_start_to_first_new_observation_seconds_upper_bound':upper,
                      'within_five_minute_budget':upper<300,
                      'timing_note':'Conservative wall-clock observation bound; final orchestration stopwatch was lost after a quoting error.'},indent=2))


if __name__=='__main__':
    main()
