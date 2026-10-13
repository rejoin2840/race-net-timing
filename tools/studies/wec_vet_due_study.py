#!/usr/bin/env python3
"""Compare fresh-energy authority against the shipped and freshness-only DUE rules.

All policies observe the same chronological calculator state. The shipped rule
uses the unaged raw tank percentage; the additive comparator uses fresh energy
but never lets high energy veto the stint path. No production inputs are changed.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import due_timing_study as study


class EnergyObserver(study.Observer):
    def __init__(self):
        super().__init__()
        self.policies = [('fresh_energy_authority', 2), ('shipped_additive_vet', 3),
                         ('fresh_additive_vet', 5)]
        self.fresh_samples = self.veto_samples = 0
        self.examples = []

    def sample(self, conn, oid, ts, ctx, cars):
        super().sample(conn, oid, ts, ctx, cars)
        if not ctx.is_race or ctx.is_finished:
            return
        raw = dict(conn.execute('SELECT car_number,fuel_pct FROM standings_current WHERE session_oid=?', (oid,)))
        threshold = study.config.CONFIG.as_dict(self.series)['VET_DUE_PCT']
        for car in cars:
            if car.laps is None:
                continue
            value = raw[car.car_number]
            shipped_vet = (self.series == 'wec' and value is not None and value <= threshold
                           and car.est_stops_left > 0)
            fresh_vet = (self.series == 'wec' and car.fuel_pct is not None and car.fuel_pct <= threshold
                         and car.est_stops_left > 0)
            shipped = bool(car.stint_fuel_due or shipped_vet)
            additive = bool(car.stint_fuel_due or fresh_vet)
            seq = self.samples[car.car_number]
            old = seq[-1]
            seq[-1] = (*old[:3], shipped, old[4], additive)
            if self.series == 'wec' and car.fuel_pct is not None:
                self.fresh_samples += 1
                if additive and car.fuel_due != 'due':
                    self.veto_samples += 1
                    if len(self.examples) < 12:
                        self.examples.append({'ts_ms': ts, 'car': car.car_number,
                                              'laps': car.laps, 'energy_pct': car.fuel_pct})

    def diagnostics(self):
        return {'telemetry_freshness_s': study.calculator.TELEMETRY_STALE_S,
                'fresh_energy_car_samples': self.fresh_samples,
                'high_energy_veto_car_samples': self.veto_samples,
                'veto_examples': self.examples}


def main(argv=None):
    return study.main(argv, observer_factory=EnergyObserver, study_path=__file__,
                      title='WEC fresh-energy DUE comparison',
                      method=study.METHOD + '; same state: fresh energy authority vs shipped additive vs fresh additive')


if __name__ == '__main__':
    raise SystemExit(main())
