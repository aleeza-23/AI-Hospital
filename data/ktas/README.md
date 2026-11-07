# KTAS emergency triage dataset

`ktas_moon2019.csv`: 1,267 adult emergency department visits with vital signs, chief complaint,
the triage nurse's KTAS level (`KTAS_RN`) and an expert-consensus reference level (`KTAS_expert`).

Source: Moon S-H, Shim JL, Park K-S, Park C-S. Triage accuracy and causes of mistriage using the
Korean Triage and Acuity Scale. PLOS ONE 14(9): e0216972 (2019).
https://doi.org/10.1371/journal.pone.0216972 (distributed on Kaggle as "Emergency Service - Triage Application").
PLOS ONE articles and supplementary data are published under CC BY 4.0.

Used by `experiments/run_triage.py` (G1). Split: `stratified_split(dev_per_level=5, test_per_level=30, seed=0)`.
