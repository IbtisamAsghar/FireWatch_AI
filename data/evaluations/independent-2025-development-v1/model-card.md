# FireWatch candidate model card

Evaluation state: development_frozen.

Final test: sealed.
No model was promoted. Research decision support only; not an emergency-response system.

## Intended target and limits

The label is later satellite-observed FRP strengthening, not physical wildfire severity.
Event IDs are thermal-activity partition proxies. Repeated snapshots are not independent fires.
Overpass, cloud/coverage, unobserved negatives, sensor/product and weather gaps limit generalization.

## Results

Development threshold-fit metrics are optimistic selection diagnostics.
- baseline-v1: n=233, events=67, precision=0.195455, recall=1, F1=0.326996, AP=0.190059, trapezoidal PR-AUC=0.181952.
- forest: n=233, events=67, precision=0.244094, recall=0.72093, F1=0.364706, AP=0.25276, trapezoidal PR-AUC=0.240937.
- forest_no_climate: n=233, events=67, precision=0.225806, recall=0.813953, F1=0.353535, AP=0.242967, trapezoidal PR-AUC=0.232732.
- forest_no_weather_climate: n=233, events=67, precision=0.223529, recall=0.883721, F1=0.356808, AP=0.26377, trapezoidal PR-AUC=0.252267.
- logistic: n=233, events=67, precision=0.323232, recall=0.744186, F1=0.450704, AP=0.307827, trapezoidal PR-AUC=0.294319.
- weighted-baseline-v1: n=233, events=67, precision=0.189427, recall=1, F1=0.318519, AP=0.192712, trapezoidal PR-AUC=0.163824.

Whole-event uncertainty requires sufficient events; absent intervals do not mean zero uncertainty.

## Priority policy

Low/Medium/High/Critical are score-scale research categories, not validated physical severity.
Development F2/F1/F0.5 objectives select ordered boundaries. Ties can collapse bands; thresholds remain provisional.
Forest fractions and logistic margins are model_score. Only an accepted sigmoid can supply a nullable probability.

## Failures and review requirements

- Threshold-cohort metrics were used for selection; they are not release performance
- Some score scales cannot distinguish all four priority bands

Release recommendation: baseline_only.
Independent limitations/model-card review and registry approval are required before ML promotion.
