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
- baseline-v1: n=210, events=55, precision=0.217172, recall=1, F1=0.356846, AP=0.204024, trapezoidal PR-AUC=0.195725.
- forest: n=210, events=55, precision=0.256198, recall=0.72093, F1=0.378049, AP=0.271859, trapezoidal PR-AUC=0.259509.
- forest_no_climate: n=210, events=55, precision=0.232143, recall=0.906977, F1=0.369668, AP=0.258159, trapezoidal PR-AUC=0.247509.
- forest_no_weather_climate: n=210, events=55, precision=0.236364, recall=0.906977, F1=0.375, AP=0.277466, trapezoidal PR-AUC=0.265795.
- logistic: n=210, events=55, precision=0.344086, recall=0.744186, F1=0.470588, AP=0.320489, trapezoidal PR-AUC=0.306856.
- weighted-baseline-v1: n=210, events=55, precision=0.210784, recall=1, F1=0.348178, AP=0.202546, trapezoidal PR-AUC=0.173439.

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
