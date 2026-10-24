# FireWatch candidate model card

Evaluation state: release_evaluated.

Final test: opened_once.
No model was promoted. Research decision support only; not an emergency-response system.

## Intended target and limits

The label is later satellite-observed FRP strengthening, not physical wildfire severity.
Event IDs are thermal-activity partition proxies. Repeated snapshots are not independent fires.
Overpass, cloud/coverage, unobserved negatives, sensor/product and weather gaps limit generalization.

## Results

Frozen methods and development-selected thresholds were evaluated on the single release holdout.
- baseline-v1: n=811, events=438, precision=0.0505464, recall=0.787234, F1=0.0949936, AP=0.050746, trapezoidal PR-AUC=0.0477785.
- forest: n=811, events=438, precision=0.0944882, recall=0.510638, F1=0.159468, AP=0.109701, trapezoidal PR-AUC=0.103065.
- forest_no_climate: n=811, events=438, precision=0.0688976, recall=0.744681, F1=0.126126, AP=0.108541, trapezoidal PR-AUC=0.101792.
- forest_no_weather_climate: n=811, events=438, precision=0.0686869, recall=0.723404, F1=0.125461, AP=0.11137, trapezoidal PR-AUC=0.104844.
- logistic: n=811, events=438, precision=0.166667, recall=0.425532, F1=0.239521, AP=0.139349, trapezoidal PR-AUC=0.13153.
- weighted-baseline-v1: n=811, events=438, precision=0.0578406, recall=0.957447, F1=0.109091, AP=0.0555941, trapezoidal PR-AUC=0.0551293.

Whole-event uncertainty requires sufficient events; absent intervals do not mean zero uncertainty.

## Priority policy

Low/Medium/High/Critical are score-scale research categories, not validated physical severity.
Development F2/F1/F0.5 objectives select ordered boundaries. Ties can collapse bands; thresholds remain provisional.
Forest fractions and logistic margins are model_score. Only an accepted sigmoid can supply a nullable probability.

## Failures and review requirements

- Held-out groups are satellite partition proxies, not independently verified fires
- No emergency-response or physical severity validity is established
- Promotion requires independent model-card/limitations review and registry approval
- Forest output is an uncalibrated model_score; probability remains null

Release recommendation: baseline_only_until_independent_approval.
Independent limitations/model-card review and registry approval are required before ML promotion.
