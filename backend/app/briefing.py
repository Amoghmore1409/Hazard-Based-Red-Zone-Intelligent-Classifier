"""SDMA briefing: structured sections. Numbers/tables come straight from platform data; the LLM (Groq,
OpenAI-compatible) only writes short sentences as JSON, grounded on those facts. Template fallback."""
import hashlib
import json
import os
import re
import time

import requests

PROMPT = """You are a disaster-management analyst briefing the State Disaster Management Authority.
Using ONLY the facts in the JSON below, write in {lang}. Return a JSON object with exactly these keys:
  "headline":  one sentence (max 25 words) stating the overall situation,
  "situation": 2-4 short bullet sentences,
  "sites":     2-3 short bullet sentences on relocation sites and the relocation plan,
  "weather":   1-3 short bullet sentences on forecast rain and official alerts,
  "actions":   3-5 objects {{"when": "Next 24 h" | "24-72 h" | "This month", "action": "..."}}.
Rules: plain sentences only - no markdown, no asterisks, no numbering. Do not invent numbers, places or
events not in the JSON. For people use population_in_red, people_by_priority, people_immediate and evacuation.
evacuation counts habitations pushed into danger by live rain/alerts: 0 means no extra escalation, never that
Red Zones are cleared. If live.status says "not refreshed yet", say the live forecast is awaiting refresh.
Never mention JSON field names or grid cells. Always write numbers as digits, never in words.
Keep habitation names exactly as given.{script}

JSON:
{facts}"""

SCRIPT = {"Hindi": " Write entirely in Devanagari script; use the region name exactly as given."}
REGION_HI = {"Uttarakhand – Chamoli & Rudraprayag": "उत्तराखंड – चमोली और रुद्रप्रयाग",
             "Odisha – Kendrapara": "ओडिशा – केंद्रपाड़ा"}

LABELS = {
    "English": {"red": "People in Red Zones", "imm": "Immediate relocation", "evac": "Under evacuation advisory",
                "sites": "Safe-site capacity", "habs": "habitations", "people": "people", "persons": "persons",
                "sites_n": "sites"},
    "Hindi": {"red": "रेड ज़ोन में लोग", "imm": "तत्काल पुनर्वास", "evac": "निकासी परामर्श के अंतर्गत",
              "sites": "सुरक्षित स्थलों की क्षमता", "habs": "बस्तियाँ", "people": "लोग", "persons": "व्यक्ति",
              "sites_n": "स्थल"},
}
HAZARD = {"English": {"landslide": "Landslide", "flood": "Flood", "cloudburst": "Cloudburst", "coastal": "Coastal erosion", "surge": "Storm surge"},
          "Hindi": {"landslide": "भूस्खलन", "flood": "बाढ़", "cloudburst": "बादल फटना", "coastal": "तटीय कटाव", "surge": "तूफ़ानी लहर"}}


def _n(x) -> str:
    """Indian digit grouping: 158000 -> 1,58,000."""
    s = str(int(round(x or 0)))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    return ",".join(re.findall(r"\d{1,2}(?=(?:\d{2})*$)", head)) + "," + tail


def _clean(v):
    if isinstance(v, str):
        return re.sub(r"[*#`]+", "", v).strip().lstrip("-•· ").strip()
    if isinstance(v, list):
        return [_clean(x) for x in v if x]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def _bullets(v) -> list[str]:
    """LLMs sometimes return a paragraph instead of a list: split it into sentences."""
    if isinstance(v, str):
        v = re.split(r"(?<=[.।!?])\s+", v)
    return [x for x in (v or []) if isinstance(x, str) and x.strip()]


def _actions(v) -> list[dict]:
    if isinstance(v, str):
        v = [{"when": "", "action": x} for x in _bullets(v)]
    return [a if isinstance(a, dict) else {"when": "", "action": str(a)} for a in (v or [])]


def _fixed(f: dict, lang: str) -> dict:
    """Parts built directly from platform numbers (never from the LLM)."""
    L = LABELS.get(lang, LABELS["English"])
    hz = HAZARD.get(lang, HAZARD["English"])
    pr = f["habitation_counts_by_priority"]
    return {
        "region": f["region"],
        "key_figures": [
            {"label": L["red"], "value": _n(f["population_in_red"]), "tone": "red"},
            {"label": L["imm"], "value": f"{pr.get('immediate', 0)} {L['habs']}",
             "sub": f"{_n(f['people_immediate'])} {L['people']}", "tone": "red"},
            {"label": L["evac"], "value": f"{f['evacuation']['habitations']} {L['habs']}",
             "sub": f"{_n(f['evacuation']['people'])} {L['people']}", "tone": "amber"},
            {"label": L["sites"], "value": f"{_n(f['site_capacity'])} {L['persons']}",
             "sub": f"{f['sites']} {L['sites_n']}", "tone": "blue"},
        ],
        "immediate": [{"name": h["name"], "district": h["district"], "people": _n(h["pop"]),
                       "hazard": hz.get(h["dominant_hazard"], h["dominant_hazard"])}
                      for h in f["immediate_habitations"][:10]],
    }


def template(f: dict) -> dict:
    imm = f["habitation_counts_by_priority"].get("immediate", 0)
    return {
        "headline": f"{_n(f['population_in_red'])} people in {f['region']} live in Red Zones; "
                    f"{imm} habitations need immediate relocation.",
        "situation": [f"{_n(f['population_in_red'])} of {_n(f['population_total'])} residents live in Red Zones.",
                      f"{imm} habitations ({_n(f['people_immediate'])} people) are prioritised for immediate relocation.",
                      f"{f['evacuation']['habitations']} habitations are under a live evacuation advisory."],
        "sites": [f"{f['sites']} safe sites can absorb {_n(f['site_capacity'])} people.",
                  f"The current plan places {_n(f['plan_assigned'])} people; {_n(f['plan_unassigned'])} still need land."],
        "weather": [f"Maximum forecast rainfall is {f['live']['max_r24']:.0f} mm in 24 h "
                    f"(peak {f['live']['max_r1']:.0f} mm/h).",
                    f"{len(f['alerts'])} official alert(s) currently apply."],
        "actions": [{"when": "Next 24 h", "action": "Verify Immediate habitations with district field teams."},
                    {"when": "24-72 h", "action": "Pre-position NDRF/SDRF teams near Immediate clusters."},
                    {"when": "This month", "action": "Start community consultations at recommended sites."}],
    }


_CACHE: dict[str, tuple[float, dict]] = {}  # ponytail: in-process 10-min cache; Groq free tier is 8k tokens/min
CACHE_S = 600


def _ask_llm(key: str, lang: str, llm_facts: dict) -> dict:
    body = {"model": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"), "temperature": 0.2,
            "reasoning_effort": "low", "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": PROMPT.format(
                lang=lang, script=SCRIPT.get(lang, ""), facts=json.dumps(llm_facts, default=str))}]}
    for attempt in range(2):
        r = requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=60,
                          headers={"Authorization": f"Bearer {key}"}, json=body)
        if r.status_code == 429 and attempt == 0:  # rate limited: wait (max 15 s) and retry once
            time.sleep(min(15.0, float(r.headers.get("retry-after", 5))))
            continue
        r.raise_for_status()
        return json.loads(r.json()["choices"][0]["message"]["content"])


def briefing(facts: dict, lang="English") -> dict:
    out = {"lang": lang, **_fixed(facts, lang)}
    llm_facts = {k: v for k, v in facts.items() if k not in ("zone_cell_counts_not_people", "active_red_cells")}
    if lang == "Hindi":
        out["region"] = llm_facts["region"] = REGION_HI.get(facts["region"], facts["region"])
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return {**out, **template(facts), "source": "template"}
    ck = hashlib.sha1(f"{lang}|{json.dumps(llm_facts, sort_keys=True, default=str)}".encode()).hexdigest()
    hit = _CACHE.get(ck)
    if hit and time.time() - hit[0] < CACHE_S:
        return {**out, **hit[1], "source": "AI (Groq)"}
    try:
        text = _ask_llm(key, lang, llm_facts)
        text = {k: text.get(k) or v for k, v in template(facts).items()}  # fill any missing section
        for k in ("situation", "sites", "weather"):
            text[k] = _bullets(text[k])
        text["actions"] = _actions(text["actions"])
        text = _clean(text)
        _CACHE[ck] = (time.time(), text)
        return {**out, **text, "source": "AI (Groq)"}
    except Exception as e:
        return {**out, **template(facts), "source": f"template (AI unavailable: {e.__class__.__name__})"}


if __name__ == "__main__":
    assert _n(158000) == "1,58,000" and _n(644200) == "6,44,200" and _n(999) == "999" and _n(1234567) == "12,34,567"
    assert _clean({"a": ["**Bold** text", "- item"]}) == {"a": ["Bold text", "item"]}
    assert _bullets("पहला वाक्य। दूसरा वाक्य।") == ["पहला वाक्य।", "दूसरा वाक्य।"] and _bullets(["a", ""]) == ["a"]
    assert _actions("Do A. Do B.") == [{"when": "", "action": "Do A."}, {"when": "", "action": "Do B."}]
    print("briefing ok")
