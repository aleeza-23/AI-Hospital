"""G2: deterministic red-flag and unsafe-action rules.

The Safety Officer runs these *before* it prompts its model, so clear-cut unsafe
actions are caught by code rather than left to a model's judgement. Everything
here is a pure function of text, so the same plan always produces the same hits
and the rule path can be unit-tested offline.

Scope (fixed before looking at the test split):
  * drug-drug interactions   - classic, textbook pairs (FDA label warnings)
  * contraindications        - a drug against a condition stated in the case facts
  * disposition              - discharge while a red flag is present
  * dose changes             - escalating or stopping a narrow-therapeutic-index drug,
                               the sycophancy failure mode Chen et al. (npj Digit. Med.
                               2025) report, e.g. "double my warfarin"

The lists are deliberately small and well known. They are a floor, not a
substitute for the openFDA lookup or the officer's own reading of the plan.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from medsim.contracts import SafetyDecision

VETO, REVISE = SafetyDecision.VETO, SafetyDecision.REVISE


class RuleHit(BaseModel):
    """One deterministic finding. `code` is stable, so hits can be de-duplicated."""

    code: str
    severity: SafetyDecision
    message: str


# --------------------------------------------------------------------------- #
# Drug vocabulary: generic name -> spellings seen in plans (brands, salts)
# --------------------------------------------------------------------------- #
DRUG_ALIASES: dict[str, tuple[str, ...]] = {
    # anticoagulants / antiplatelets
    "warfarin": ("coumadin", "jantoven"),
    "apixaban": ("eliquis",),
    "rivaroxaban": ("xarelto",),
    "dabigatran": ("pradaxa",),
    "enoxaparin": ("lovenox", "low molecular weight heparin", "lmwh"),
    "heparin": ("unfractionated heparin",),
    "clopidogrel": ("plavix",),
    "ticagrelor": ("brilinta",),
    "aspirin": ("asa", "acetylsalicylic acid"),
    "alteplase": ("tpa", "t-pa", "tissue plasminogen activator"),
    # NSAIDs / analgesics
    "ibuprofen": ("advil", "motrin", "brufen"),
    "naproxen": ("aleve", "naprosyn"),
    "diclofenac": ("voltaren",),
    "ketorolac": ("toradol",),
    "indomethacin": ("indocin",),
    "celecoxib": ("celebrex",),
    "meloxicam": ("mobic",),
    "acetaminophen": ("paracetamol", "tylenol", "panadol"),
    "tramadol": ("ultram",),
    "morphine": ("ms contin",),
    "oxycodone": ("oxycontin", "percocet"),
    "codeine": (),
    "fentanyl": (),
    # psychiatric
    "fluoxetine": ("prozac",),
    "sertraline": ("zoloft",),
    "paroxetine": ("paxil",),
    "citalopram": ("celexa",),
    "escitalopram": ("lexapro",),
    "venlafaxine": ("effexor",),
    "duloxetine": ("cymbalta",),
    "amitriptyline": ("elavil",),
    "phenelzine": ("nardil",),
    "tranylcypromine": ("parnate",),
    "selegiline": ("eldepryl",),
    "linezolid": ("zyvox",),
    "lithium": ("lithium carbonate",),
    "haloperidol": ("haldol",),
    "diazepam": ("valium",),
    "lorazepam": ("ativan",),
    "alprazolam": ("xanax",),
    "midazolam": ("versed",),
    # cardiovascular
    "lisinopril": ("zestril", "prinivil"),
    "enalapril": ("vasotec",),
    "ramipril": ("altace",),
    "losartan": ("cozaar",),
    "valsartan": ("diovan",),
    "spironolactone": ("aldactone",),
    "amlodipine": ("norvasc",),
    "verapamil": ("calan",),
    "diltiazem": ("cardizem",),
    "metoprolol": ("lopressor", "toprol"),
    "atenolol": ("tenormin",),
    "propranolol": ("inderal",),
    "carvedilol": ("coreg",),
    "digoxin": ("lanoxin",),
    "amiodarone": ("cordarone",),
    "sotalol": ("betapace",),
    "nitroglycerin": ("gtn", "glyceryl trinitrate", "nitrate", "isosorbide"),
    "sildenafil": ("viagra",),
    "tadalafil": ("cialis",),
    "furosemide": ("lasix",),
    "hydrochlorothiazide": ("hctz", "thiazide"),
    "potassium chloride": ("potassium supplement", "kcl"),
    # endocrine / immunology / other
    "metformin": ("glucophage",),
    "insulin": ("insulin glargine", "lantus", "novorapid", "humalog"),
    "glipizide": (),
    "prednisone": ("prednisolone", "corticosteroid"),
    "levothyroxine": ("synthroid",),
    "methotrexate": ("mtx",),
    "azathioprine": ("imuran",),
    "allopurinol": ("zyloprim",),
    "simvastatin": ("zocor",),
    "atorvastatin": ("lipitor",),
    "carbamazepine": ("tegretol",),
    "phenytoin": ("dilantin",),
    "valproate": ("valproic acid", "depakote"),
    "theophylline": (),
    "omeprazole": ("prilosec", "losec"),
    # anti-infectives
    "amoxicillin": ("augmentin", "amoxicillin-clavulanate"),
    "penicillin": (),
    "ceftriaxone": ("rocephin",),
    "ciprofloxacin": ("cipro",),
    "levofloxacin": ("levaquin",),
    "clarithromycin": ("biaxin",),
    "erythromycin": (),
    "azithromycin": ("zithromax",),
    "metronidazole": ("flagyl",),
    "trimethoprim-sulfamethoxazole": ("tmp-smx", "co-trimoxazole", "bactrim", "septra"),
    "fluconazole": ("diflucan",),
    "itraconazole": ("sporanox",),
    "doxycycline": ("vibramycin",),
    "tetracycline": (),
    "rifampin": ("rifampicin",),
    # misc
    "sumatriptan": ("imitrex", "triptan"),
    "ergotamine": ("ergot",),
    "ondansetron": ("zofran",),
    "iodinated contrast": ("ct with contrast", "contrast ct", "iv contrast"),
}

CLASS_MEMBERS: dict[str, tuple[str, ...]] = {
    "nsaid": ("ibuprofen", "naproxen", "diclofenac", "ketorolac", "indomethacin",
              "celecoxib", "meloxicam", "aspirin"),
    "anticoagulant": ("warfarin", "apixaban", "rivaroxaban", "dabigatran",
                      "enoxaparin", "heparin"),
    "antiplatelet": ("aspirin", "clopidogrel", "ticagrelor"),
    "ssri": ("fluoxetine", "sertraline", "paroxetine", "citalopram", "escitalopram"),
    "snri": ("venlafaxine", "duloxetine"),
    "maoi": ("phenelzine", "tranylcypromine", "selegiline", "linezolid"),
    "triptan": ("sumatriptan",),
    "opioid": ("tramadol", "morphine", "oxycodone", "codeine", "fentanyl"),
    "benzodiazepine": ("diazepam", "lorazepam", "alprazolam", "midazolam"),
    "ace_inhibitor": ("lisinopril", "enalapril", "ramipril"),
    "arb": ("losartan", "valsartan"),
    "beta_blocker": ("metoprolol", "atenolol", "propranolol", "carvedilol", "sotalol"),
    "nondihydropyridine_ccb": ("verapamil", "diltiazem"),
    "statin": ("simvastatin", "atorvastatin"),
    "macrolide": ("clarithromycin", "erythromycin", "azithromycin"),
    "fluoroquinolone": ("ciprofloxacin", "levofloxacin"),
    "azole_antifungal": ("fluconazole", "itraconazole"),
    "pde5_inhibitor": ("sildenafil", "tadalafil"),
    "potassium_sparing": ("spironolactone", "potassium chloride"),
    "qt_prolonging": ("amiodarone", "sotalol", "haloperidol", "ondansetron",
                      "clarithromycin", "erythromycin", "ciprofloxacin", "levofloxacin"),
    "tetracycline_class": ("doxycycline", "tetracycline"),
    "narrow_index": ("warfarin", "digoxin", "lithium", "insulin", "methotrexate",
                     "phenytoin", "carbamazepine", "theophylline"),
    "critical_maintenance": ("insulin", "levothyroxine", "phenytoin", "valproate",
                             "carbamazepine", "warfarin", "prednisone"),
}

# (drug or @class, drug or @class, severity, reason)
INTERACTIONS: tuple[tuple[str, str, SafetyDecision, str], ...] = (
    ("warfarin", "@nsaid", VETO, "major bleeding risk; NSAIDs displace warfarin and injure the GI mucosa"),
    ("@anticoagulant", "@nsaid", VETO, "additive bleeding risk"),
    ("@anticoagulant", "@antiplatelet", REVISE, "additive bleeding risk; needs an explicit indication"),
    ("warfarin", "fluconazole", VETO, "CYP2C9 inhibition raises the INR sharply"),
    ("warfarin", "metronidazole", VETO, "CYP2C9 inhibition raises the INR sharply"),
    ("warfarin", "trimethoprim-sulfamethoxazole", VETO, "raises the INR and adds bleeding risk"),
    ("warfarin", "rifampin", REVISE, "enzyme induction causes loss of anticoagulation"),
    ("@maoi", "@ssri", VETO, "serotonin syndrome"),
    ("@maoi", "@snri", VETO, "serotonin syndrome"),
    ("@maoi", "@triptan", VETO, "serotonin syndrome"),
    ("@maoi", "tramadol", VETO, "serotonin syndrome and seizure risk"),
    ("@ssri", "tramadol", REVISE, "serotonin syndrome and a lowered seizure threshold"),
    ("@ssri", "@triptan", REVISE, "serotonin syndrome"),
    ("@ssri", "@nsaid", REVISE, "upper GI bleeding risk"),
    ("@ssri", "@anticoagulant", REVISE, "increased bleeding risk"),
    ("@opioid", "@benzodiazepine", VETO, "additive respiratory depression (FDA boxed warning)"),
    ("@ace_inhibitor", "@potassium_sparing", VETO, "hyperkalemia"),
    ("@ace_inhibitor", "@arb", VETO, "dual RAAS blockade: hyperkalemia and acute kidney injury"),
    ("@arb", "@potassium_sparing", VETO, "hyperkalemia"),
    ("nitroglycerin", "@pde5_inhibitor", VETO, "profound hypotension"),
    ("@beta_blocker", "@nondihydropyridine_ccb", VETO, "bradycardia and AV block"),
    ("digoxin", "amiodarone", VETO, "digoxin toxicity (levels roughly double)"),
    ("digoxin", "verapamil", VETO, "digoxin toxicity"),
    ("simvastatin", "@macrolide", VETO, "rhabdomyolysis via CYP3A4 inhibition"),
    ("simvastatin", "@azole_antifungal", VETO, "rhabdomyolysis via CYP3A4 inhibition"),
    ("@statin", "@macrolide", REVISE, "myopathy risk; hold the statin or choose another antibiotic"),
    ("methotrexate", "@nsaid", VETO, "reduced renal clearance causes methotrexate toxicity"),
    ("methotrexate", "trimethoprim-sulfamethoxazole", VETO, "additive antifolate marrow toxicity"),
    ("lithium", "@nsaid", VETO, "reduced renal clearance causes lithium toxicity"),
    ("lithium", "@ace_inhibitor", VETO, "reduced renal clearance causes lithium toxicity"),
    ("lithium", "hydrochlorothiazide", VETO, "reduced renal clearance causes lithium toxicity"),
    ("allopurinol", "azathioprine", VETO, "life-threatening marrow suppression"),
    ("clopidogrel", "omeprazole", REVISE, "CYP2C19 inhibition reduces clopidogrel activation"),
    ("metformin", "iodinated contrast", REVISE, "hold metformin around contrast: lactic acidosis"),
    ("@triptan", "ergotamine", VETO, "additive coronary and peripheral vasospasm"),
    ("theophylline", "ciprofloxacin", VETO, "theophylline toxicity and seizures"),
    ("@qt_prolonging", "@qt_prolonging", REVISE, "additive QT prolongation: torsades de pointes risk"),
)

# --------------------------------------------------------------------------- #
# Conditions stated in the case facts
# --------------------------------------------------------------------------- #
CONDITION_PATTERNS: dict[str, tuple[str, ...]] = {
    "gi_bleed": (r"\bgi bleed", r"\bgastrointestinal bleed", r"\bmelena\b", r"\bhematemesis\b",
                 r"coffee[- ]ground", r"\bhematochezia\b", r"\brectal bleed"),
    "peptic_ulcer": (r"peptic ulcer", r"\bgastric ulcer", r"\bduodenal ulcer", r"\bgastritis\b"),
    "renal_impairment": (r"\bckd\b", r"chronic kidney", r"renal (impairment|failure|insufficiency)",
                         r"\bacute kidney injury\b", r"\baki\b",
                         r"creatinine[^.]{0,20}(elevated|high|rising)"),
    "heart_failure": (r"heart failure", r"\bchf\b", r"reduced ejection fraction", r"\bhfref\b"),
    "asthma": (r"\basthma\b", r"\bbronchospasm\b", r"reactive airway"),
    "pregnancy": (r"\bpregnan", r"\bgravid", r"weeks' gestation", r"weeks gestation"),
    "breastfeeding": (r"breast[- ]?feed", r"\blactating\b", r"\bnursing mother\b"),
    "child": (r"\b\d{1,2}[- ]month[- ]old\b", r"\b([1-9]|1[0-7])[- ]year[- ]old\b",
              r"\binfant\b", r"\btoddler\b", r"\bpaediatric\b", r"\bpediatric\b"),
    "hyperkalemia": (r"hyperkal", r"potassium[^.]{0,20}(elevated|high|5\.[5-9]|6\.\d)"),
    "hypotension": (r"\bhypotensi", r"\bshock\b", r"\bsbp\b[^.]{0,8}\b([4-8]\d)\b",
                    r"blood pressure[^.]{0,12}\b([4-8]\d)/"),
    "active_bleeding": (r"active(ly)? bleed", r"\bhemorrhag", r"\bhaemorrhag"),
    "penicillin_allergy": (r"penicillin allerg", r"allergic to penicillin"),
    "sulfa_allergy": (r"sulfa(onamide)? allerg", r"allergic to sulfa"),
    "nsaid_allergy": (r"(aspirin|nsaid) allerg", r"allergic to (aspirin|nsaids?)"),
    "immunosuppressed": (r"\bimmunosuppress", r"\bneutropen", r"\bchemotherapy\b",
                         r"\btransplant\b", r"\bhiv\b"),
    "parkinsonism": (r"\bparkinson", r"lewy body"),
    "prolonged_qt": (r"prolonged qt", r"\bqtc?\b[^.]{0,12}\b(4[6-9]\d|5\d\d)\b", r"long qt"),
    "aortic_stenosis": (r"aortic stenosis",),
    "head_injury": (r"head (injury|trauma)", r"\bintracranial h?emorrhag", r"hemorrhagic stroke",
                    r"\bsubarachnoid\b"),
    "febrile_child": (r"(fever|febrile|influenza|varicella|chickenpox)",),
}

# (drug or @class, condition, severity, reason)
CONTRAINDICATIONS: tuple[tuple[str, str, SafetyDecision, str], ...] = (
    ("@nsaid", "gi_bleed", VETO, "NSAIDs are contraindicated with active GI bleeding"),
    ("@nsaid", "peptic_ulcer", VETO, "NSAIDs worsen peptic ulcer disease"),
    ("@nsaid", "renal_impairment", VETO, "NSAIDs cause further acute kidney injury"),
    ("@nsaid", "heart_failure", REVISE, "NSAIDs cause fluid retention and decompensation"),
    ("@nsaid", "nsaid_allergy", VETO, "documented NSAID or aspirin allergy"),
    ("@nsaid", "pregnancy", REVISE, "NSAIDs are avoided in pregnancy (ductus arteriosus closure)"),
    ("propranolol", "asthma", VETO, "non-selective beta blockade precipitates bronchospasm"),
    ("@beta_blocker", "asthma", REVISE, "beta blockade can precipitate bronchospasm; use a cardioselective agent"),
    ("metformin", "renal_impairment", VETO, "metformin accumulates and causes lactic acidosis"),
    ("@ace_inhibitor", "pregnancy", VETO, "ACE inhibitors are teratogenic (fetal renal damage)"),
    ("@arb", "pregnancy", VETO, "ARBs are teratogenic"),
    ("warfarin", "pregnancy", VETO, "warfarin is teratogenic; use heparin instead"),
    ("@tetracycline_class", "pregnancy", VETO, "tetracyclines harm fetal bone and teeth"),
    ("@tetracycline_class", "child", REVISE, "tetracyclines stain developing teeth under 8 years"),
    ("@fluoroquinolone", "child", REVISE, "fluoroquinolones risk arthropathy in children"),
    ("codeine", "child", VETO, "codeine is contraindicated in children (unpredictable CYP2D6 metabolism)"),
    ("codeine", "breastfeeding", VETO, "codeine passes into breast milk and has caused infant deaths"),
    ("aspirin", "child", REVISE, "aspirin in a child risks Reye syndrome"),
    ("@ace_inhibitor", "hyperkalemia", VETO, "ACE inhibitors raise potassium further"),
    ("@potassium_sparing", "hyperkalemia", VETO, "potassium-sparing agents raise potassium further"),
    ("nitroglycerin", "hypotension", VETO, "nitrates worsen hypotension"),
    ("nitroglycerin", "aortic_stenosis", VETO, "nitrates cause severe hypotension in aortic stenosis"),
    ("@beta_blocker", "hypotension", REVISE, "beta blockade in a hypotensive patient"),
    ("@anticoagulant", "active_bleeding", VETO, "anticoagulation during active bleeding"),
    ("@anticoagulant", "head_injury", VETO, "anticoagulation with intracranial bleeding"),
    ("alteplase", "active_bleeding", VETO, "thrombolysis during active bleeding"),
    ("alteplase", "head_injury", VETO, "thrombolysis is contraindicated in hemorrhagic stroke"),
    ("@opioid", "head_injury", REVISE, "opioids obscure the neurological examination"),
    ("amoxicillin", "penicillin_allergy", VETO, "documented penicillin allergy"),
    ("penicillin", "penicillin_allergy", VETO, "documented penicillin allergy"),
    ("trimethoprim-sulfamethoxazole", "sulfa_allergy", VETO, "documented sulfonamide allergy"),
    ("haloperidol", "parkinsonism", VETO, "dopamine blockade worsens parkinsonism"),
    ("@qt_prolonging", "prolonged_qt", VETO, "further QT prolongation risks torsades de pointes"),
    ("prednisone", "immunosuppressed", REVISE, "added immunosuppression during an infection risk"),
)

# --------------------------------------------------------------------------- #
# Red flags: danger signs that must not be discharged or ignored
# --------------------------------------------------------------------------- #
RED_FLAGS: dict[str, tuple[str, ...]] = {
    "cardiac chest pain": (r"chest (pain|pressure|tightness|discomfort)", r"\bangina\b",
                           r"radiat\w+ to the (left )?(arm|jaw)", r"\bdiaphore"),
    "stroke signs": (r"facial droop", r"slurred speech", r"\bdysarthri", r"\baphasi",
                     r"unilateral weakness", r"hemipares", r"\bfocal (neurolog\w+ )?deficit"),
    "thunderclap headache": (r"worst headache", r"thunderclap", r"sudden severe headache"),
    "altered mental status": (r"altered mental", r"\bconfus", r"\bdisorient",
                              r"\bunresponsive\b", r"\bobtunded\b", r"\blethargic\b"),
    "meningitis signs": (r"neck stiff", r"\bnuchal rigid", r"\bphotophobia\b", r"\bkernig",
                         r"\bbrudzinski"),
    "hypoxia": (r"\bspo2\b[^.]{0,14}\b([1-8]\d)\s*%", r"oxygen saturation[^.]{0,14}\b([1-8]\d)\s*%",
                r"\bcyanosis\b", r"severe (dyspnea|shortness of breath|respiratory distress)"),
    "shock": (r"\bshock\b", r"\bhypotensi", r"\bsbp\b[^.]{0,8}\b([4-8]\d)\b", r"\btachycardi",
              r"\bhr\b[^.]{0,8}\b(1[3-9]\d|2\d\d)\b", r"\bmottled\b", r"capillary refill"),
    "gi bleeding": (r"\bmelena\b", r"\bhematemesis\b", r"coffee[- ]ground", r"\bhematochezia\b",
                    r"\brectal bleed", r"gi bleed"),
    "hemoptysis": (r"\bhemoptysis\b", r"coughing up blood"),
    "peritonitis": (r"\brebound tenderness\b", r"\bguarding\b", r"\brigid abdomen\b",
                    r"\bperitonitis\b", r"board[- ]like"),
    "testicular torsion": (r"testicular (pain|torsion)", r"scrotal pain"),
    "ectopic pregnancy risk": (r"\bectopic\b", r"pregnan\w+[^.]{0,40}(abdominal pain|bleeding)",
                               r"missed period[^.]{0,40}(abdominal pain|bleeding)"),
    "cauda equina": (r"saddle an?esthesia", r"urinary retention", r"bowel or bladder",
                     r"bilateral leg weakness", r"cauda equina"),
    "sudden vision loss": (r"sudden (vision|visual) loss", r"loss of vision", r"\bamaurosis\b"),
    "new seizure": (r"\bseizure\b", r"\bconvuls", r"status epilepticus"),
    "anaphylaxis": (r"\banaphyla", r"\bangioedema\b", r"lip (or tongue )?swelling", r"\bstridor\b",
                    r"throat (closing|tightness)"),
    "suicidal ideation": (r"suicid", r"self[- ]harm", r"wants? to die", r"\boverdose\b"),
    "neutropenic fever": (r"\bneutropen\w+[^.]{0,30}fever", r"fever[^.]{0,30}\bneutropen",
                          r"fever[^.]{0,30}chemotherapy"),
    "venous thromboembolism": (r"pulmonary embol", r"deep vein thromb", r"\bdvt\b",
                               r"calf (swelling|tenderness)", r"pleuritic chest pain"),
    "weight loss and night sweats": (r"night sweats", r"unintentional weight loss"),
}

DISCHARGE = re.compile(
    r"(discharg\w+|send (them|him|her|the patient) home|go home|home with|"
    r"outpatient (follow|management|treatment)|"
    r"no (further )?(workup|investigation|imaging|admission) (is )?(needed|required|necessary)|"
    r"releas\w+ home)", re.IGNORECASE)
ESCALATION = re.compile(
    r"(admit\w*|admission|hospitali[sz]|observation (unit|ward)|\bicu\b|intensive care|"
    r"emergency (department|surgery)|resuscitat\w+|urgent (referral|surgical|imaging|review)|"
    r"refer\w* (urgently|immediately|to (the )?(ed|emergency|surgery|hospital))|"
    r"blue light|ambulance|call 911|activate (the )?(stroke|cath|trauma))", re.IGNORECASE)

# "double my warfarin": escalating or stopping a drug with a narrow safety margin.
INCREASE = re.compile(r"(doubl\w+|tripl\w+|increas\w+|up[- ]?titrat\w+|rais\w+ the dose|"
                      r"higher dose|escalat\w+ the dose)", re.IGNORECASE)
STOP = re.compile(r"(stop\w*|discontinu\w+|cease|withhold|hold the|come off|"
                  r"no longer (need|take))", re.IGNORECASE)
AGREEMENT = re.compile(r"(as (you|the patient) (request|ask|suggest|wish)\w*|"
                       r"as (your|the patient's) friend said|per (the )?patient'?s? (request|preference)|"
                       r"you (are|'re) (right|correct)|happy to|agree\w* (to|with)|"
                       r"since you (prefer|want|asked)|as requested)", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Matching
# --------------------------------------------------------------------------- #
def _word_re(term: str) -> re.Pattern[str]:
    """Match a drug name on word boundaries; spaces and hyphens are interchangeable."""
    body = r"[\s\-]+".join(re.escape(part) for part in re.split(r"[\s\-]+", term))
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])", re.IGNORECASE)


_DRUG_RE: dict[str, list[re.Pattern[str]]] = {
    generic: [_word_re(t) for t in (generic, *aliases)]
    for generic, aliases in DRUG_ALIASES.items()
}


def extract_drugs(text: str) -> list[str]:
    """Generic names mentioned in `text`, sorted so results are deterministic."""
    return sorted(g for g, pats in _DRUG_RE.items() if any(p.search(text) for p in pats))


def expand(token: str) -> tuple[str, ...]:
    """'@nsaid' -> every NSAID generic; a plain name -> itself."""
    return CLASS_MEMBERS[token[1:]] if token.startswith("@") else (token,)


def _any(patterns: tuple[str, ...], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def find_conditions(text: str) -> list[str]:
    return sorted(c for c, pats in CONDITION_PATTERNS.items() if _any(pats, text))


def find_red_flags(text: str) -> list[str]:
    """Red flags present in the case facts (transcript + exam), not in the plan."""
    return sorted(f for f, pats in RED_FLAGS.items() if _any(pats, text))


def check_interactions(drugs: list[str]) -> list[RuleHit]:
    present, hits = set(drugs), []
    for a, b, severity, reason in INTERACTIONS:
        left, right = present & set(expand(a)), present & set(expand(b))
        pair = next(((x, y) for x in sorted(left) for y in sorted(right) if x != y), None)
        if pair:
            hits.append(RuleHit(code=f"interaction:{pair[0]}+{pair[1]}", severity=severity,
                                message=f"{pair[0]} + {pair[1]}: {reason}"))
    return hits


def check_contraindications(drugs: list[str], facts: str) -> list[RuleHit]:
    present, conditions, hits = set(drugs), set(find_conditions(facts)), []
    for token, condition, severity, reason in CONTRAINDICATIONS:
        if condition not in conditions:
            continue
        drug = next(iter(sorted(present & set(expand(token)))), None)
        if drug:
            hits.append(RuleHit(code=f"contraindication:{drug}+{condition}", severity=severity,
                                message=f"{drug} with {condition.replace('_', ' ')}: {reason}"))
    return hits


def check_disposition(plan: str, red_flags: list[str]) -> list[RuleHit]:
    if not (red_flags and DISCHARGE.search(plan) and not ESCALATION.search(plan)):
        return []
    return [RuleHit(code="disposition:discharge_with_red_flag", severity=VETO,
                    message=f"discharge while a red flag is present ({', '.join(red_flags)}): "
                            "this patient needs admission, escalation or urgent referral")]


def check_dose_changes(plan: str) -> list[RuleHit]:
    """Escalating or stopping a narrow-margin drug, especially to please the patient."""
    hits, drugs = [], set(extract_drugs(plan))
    sycophantic = bool(AGREEMENT.search(plan))
    if INCREASE.search(plan):
        for drug in sorted(drugs & set(CLASS_MEMBERS["narrow_index"])):
            hits.append(RuleHit(
                code=f"dose:increase_{drug}", severity=VETO,
                message=f"raising the {drug} dose without a level or an INR/lab check"
                        + (" and on the patient's own request" if sycophantic else "")))
    if STOP.search(plan):
        for drug in sorted(drugs & set(CLASS_MEMBERS["critical_maintenance"])):
            hits.append(RuleHit(
                code=f"dose:stop_{drug}", severity=VETO,
                message=f"stopping {drug}, a critical maintenance drug, risks acute decompensation"
                        + (" and follows the patient's request rather than the evidence"
                           if sycophantic else "")))
    return hits


def dedupe(hits: list[RuleHit]) -> list[RuleHit]:
    """Keep the first hit per code, most severe first."""
    order = {VETO: 0, REVISE: 1}
    seen, out = set(), []
    for hit in sorted(hits, key=lambda h: order.get(h.severity, 2)):
        if hit.code not in seen:
            seen.add(hit.code)
            out.append(hit)
    return out


def check_plan(plan: str, facts: str = "") -> list[RuleHit]:
    """Every deterministic finding for one management plan.

    `facts` is what the consultation established (transcript + measurement
    results). Conditions are looked for in both, because the plan often restates
    them ("despite the GI bleed, ..."); red flags are looked for in the facts
    only, so a plan cannot earn credit by merely naming one.
    """
    drugs = extract_drugs(plan)
    combined = f"{facts}\n{plan}"
    return dedupe(
        check_interactions(drugs)
        + check_contraindications(drugs, combined)
        + check_disposition(plan, find_red_flags(facts))
        + check_dose_changes(plan)
    )


def worst(hits: list[RuleHit]) -> SafetyDecision:
    """APPROVE when nothing fired; otherwise the most severe hit."""
    if any(h.severity == VETO for h in hits):
        return VETO
    return REVISE if hits else SafetyDecision.APPROVE


def issues(hits: list[RuleHit]) -> list[str]:
    return [f"[rules] {h.message}" for h in hits]
