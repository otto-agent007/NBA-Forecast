# October 23 forecast review

All 12 final boxscores were reconciled. The daily review is available on the
dashboard's Performance tab with October 23 selected. This package preserves
the review, an independent calculation, source checks and a separate reproduction
of the rotation defect. SHA 256 values are in `manifest.json`.

The selected final forecasts used 0.2.1 (1 game),0.4.0 (5),0.4.1 (1), and 0.4.2 (5).
None used 0.5, which was activated after these games started. Latest preliminary
and final runs were selected separately before reading outcomes, checking source
hashes, real creation/cutoff times, scheduled teams and tipoff. Existing forecasts
and their results were not rewritten.

## Results

| Measure | Result |
|---|---:|
| Matched projected players |292|
| Player point MAE |4.250|
| Minutes MAE |5.882|
| FGA MAE |2.732|
| FTA MAE |1.556|
| Old nominal 80% interval coverage |187/292 =64.04%|
| Same 289 player pairs, preliminary→final MAE |4.296→4.269|

There were 305 projected rows:292 matched and 13 absent from final boxes. Missing
rows remain unscored. The 292 include 254 played players and 38 explicit DNPs.
Those DNPs received 286.08 forecast minutes and 97.77 points. All 65 out forecasts
were absent from the provider boxes, so they are not counted as independently
verified correct zeros. There were 39 actual rows with withheld projections and
six actual-only DNP rows. Played-only scoring bias was +0.019 points, which does
not support indiscriminately reducing player scoring rates after this slate.

| Game | Actual away–home | Final projected total | Provenance/status |
|---|---:|---:|---|
|CHI–MIL|132–125|Unavailable|Derived; MIL withheld|
|NYK–BOS|103–107|222.67|Derived; partial|
|ATL–CHA|128–81|211.16|Derived; partial|
|DAL–IND|121–105|239.16|Derived; partial|
|MIN–ORL|115–93|250.37|Derived; partial|
|TOR–WAS|112–102|243.33|Derived; partial|
|DET–MIA|128–111|242.39|Saved; partial|
|HOU–SAS|114–110|239.79|Saved; partial|
|NOP–UTA|119–120|257.37|Saved; partial|
|MEM–GSW|111–108|235.96|Saved; partial|
|LAC–LAL|117–125|216.15|Saved; complete|
|POR–SAC|128–120|236.51|Saved; partial|

The first six forecasts have no saved aggregate total. Derived sums are not
presented as originally issued totals. DET–MIA retains its saved partial label,
even though a newer coverage policy could label the same player sum complete.
Only one complete game total was saved: its absolute error was 25.85. The five
saved complete team totals had MAE 9.23. These samples are too small for a broad
claim about team-total accuracy. ATL–CHA also illustrates cancellation: its
combined total was close, while ATL was underpredicted 17.91 and CHA overpredicted 20.07.

## First correction to validate

Nine ATL players were protected as core rotation players, reserving 242.503
minutes before allocating a 240-minute game. The resulting bench scaling forced
Jock Landale from 19.236 baseline minutes to 0 (actual 18 minutes/8 points) and
Kingston Flemings from 15.233 to 0 (actual 25 minutes/5 points). Neither had a manual
restriction. The displayed projected lineup correctly had five starters; the
numerical protection rule was broader.

Corey Kispert and Asa Newell retained protection from April 12 starts despite
newer current-season DNP evidence. MEM supplied corroborating cases: Hendricks,
Prosper and Mashack received 51.98 combined projected minutes and 19.59 points but
all were explicit DNPs; their protected roles also came from April starts.

A separate posthoc replay reproduced the ATL zero-minute defect under the active
0.5 model, using the original pregame roster/history and no target-game outcomes.
The existing baseline fills 240 minutes before the new injury allocator runs,
leaving no capacity to restore those erased bench roles. This remains an open
model correction, not a correction completed by this reporting change.

Test freshness and DNP-aware core protection, and feasible allocation when core
roles exceed 240 minutes. Preserve legitimate opening-season priors: Podziemski
had no current-season history and played 33 minutes against 28.45 forecast.

## Other follow-ups

- Review participation and DNP risk using multiple dates; availability alone
  does not establish that a player will enter the rotation.
- Investigate Duren/Reed, Toppin/Brown, Queen/Missi and McBride minutes changes
  before treating them as persistent roles. Do not infer injury/foul/coaching causes.
- Separate opportunity from conversion: Edwards took 23 FGA versus 19.93 forecast
  yet scored 12.68 fewer points. His accounting decomposition was−1.25 minutes,
  +5.51 attempt volume and−16.94 conversion/free throws. Reducing his shot
  opportunity rate from this result would address the wrong observed component.
- Track original saved intervals by model version. Tonight's old heuristic
  intervals do not validate 0.5's new empirical intervals.
- Review Walter Clayton Jr.'s reported DAL played and MEM DNP entries on the
  same date. The report flags this roster-source discrepancy explicitly.

The source audit verified 20 original ESPN scoreboard/summary hashes and all 12
scheduled game identities. All games ended in four periods. Played minutes have
whole-minute source precision. No reconciled play-by-play was captured for these
games; detailed foul, injury and blowout-rest explanations remain unverified.

Automatic reviews now run after result collection and retain content-hash
revisions. Later ingested statistical corrections rebuild older saved dates.
New verified actuals enter the existing history/calibration pipeline. No point
model parameters were tuned from this single day's outcomes.
