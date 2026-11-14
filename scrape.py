"""Collect SDE-1 style jobs for companies in companies.txt -> docs/jobs.json"""
import hashlib, html, json, re, sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).parent
OUT = ROOT / "docs" / "jobs.json"
UA = {"User-Agent": "Mozilla/5.0 (personal job dashboard)"}

CITIES = {"Bengaluru": "bengaluru|bangalore", "Hyderabad": "hyderabad", "Pune": "pune", "Chennai": "chennai",
          "Mumbai": "mumbai|navi mumbai", "Gurugram": "gurgaon|gurugram", "Noida": "noida", "Delhi": "delhi",
          "Kolkata": "kolkata", "Ahmedabad": "ahmedabad", "Kochi": "kochi", "Coimbatore": "coimbatore",
          "Jaipur": "jaipur", "Indore": "indore", "Thiruvananthapuram": "thiruvananthapuram|trivandrum"}
INDIA = re.compile("india|" + "|".join(CITIES.values()) + r"|\d+ locations", re.I)
TITLE_OK = re.compile(r"software (development )?engineer|software dev engineer|\bsde\b|software developer|member of technical staff|\bmts\b|"
                      r"associate (software )?engineer|(back|front)[- ]?end engineer|full[- ]?stack|application developer|"
                      r"programmer analyst|graduate engineer", re.I)
TITLE_BAD = re.compile(r"senior|\bsr\b|staff|principal|lead\b|manager|architect|director|head\b|intern\b|trainee|machine learning|\bml\b|"
                       r"\bai\b|data|\bqa\b|test|sdet|security|devops|\bsre\b|reliability|support|solution|sales|embedded|firmware|"
                       r"hardware|verification|validation|research|scientist|\b(ii|iii|iv|2|3|4|5)\b", re.I)
KEYWORDS = ["Java", "Python", "Go", "Golang", "C++", "C#", ".NET", "JavaScript", "TypeScript", "React", "Angular", "Node.js",
            "Spring Boot", "Spring", "Kotlin", "Swift", "Ruby", "Rails", "PHP", "SQL", "MySQL", "PostgreSQL", "MongoDB",
            "Redis", "Kafka", "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Microservices", "REST", "GraphQL",
            "System Design", "Data Structures", "Algorithms", "CI/CD", "Git", "Linux", "Terraform", "Elasticsearch"]

def clean(t):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(html.unescape(t or "")))).strip()

# ---- ATS adapters: each returns dicts with title, location, url, desc, ext_id -------------------
def greenhouse(c):
    r = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{c['slug']}/jobs?content=true", headers=UA, timeout=30)
    r.raise_for_status()
    return [dict(title=j["title"], location=(j.get("location") or {}).get("name", ""), url=j["absolute_url"],
                 desc=clean(j.get("content")), ext_id=str(j["id"])) for j in r.json().get("jobs", [])]

def lever(c):
    r = requests.get(f"https://api.lever.co/v0/postings/{c['slug']}?mode=json", headers=UA, timeout=30)
    r.raise_for_status()
    return [dict(title=j["text"], location=(j.get("categories") or {}).get("location") or "", url=j["hostedUrl"],
                 desc=j.get("descriptionPlain") or "", ext_id=j["id"]) for j in r.json()]

def smartrecruiters(c):
    r = requests.get(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings?country=in&q=software&limit=100",
                     headers=UA, timeout=30)
    r.raise_for_status()
    return [dict(title=j["name"], location=f"{j['location'].get('city','')}, India", desc="", ext_id=j["id"],
                 url=f"https://jobs.smartrecruiters.com/{c['slug']}/{j['id']}") for j in r.json().get("content", [])]

def workday(c):
    tenant, wd, site = c["slug"].split(":")
    base, out = f"https://{tenant}.{wd}.myworkdayjobs.com", []
    for off in range(0, 100, 20):
        r = requests.post(f"{base}/wday/cxs/{tenant}/{site}/jobs", headers=UA, timeout=30,
                          json={"appliedFacets": {}, "limit": 20, "offset": off, "searchText": "software engineer india"})
        r.raise_for_status()
        posts = r.json().get("jobPostings", [])
        if not posts:
            break
        out += [dict(title=p["title"], location=p.get("locationsText", ""), desc="", ext_id=p["externalPath"],
                     url=f"{base}/{site}{p['externalPath']}") for p in posts]
    return out

def amazon(c):
    r = requests.get("https://www.amazon.jobs/en/search.json", headers=UA, timeout=30, params={
        "base_query": "software development engineer", "loc_query": "India", "country": "IND", "result_limit": 100})
    r.raise_for_status()
    return [dict(title=j["title"], location=j.get("location", "India"), url="https://www.amazon.jobs" + j["job_path"],
                 desc=clean(j.get("basic_qualifications", "") + " " + j.get("description_short", "")),
                 ext_id=j.get("id_icims") or j["job_path"]) for j in r.json().get("jobs", [])]

ADAPTERS = dict(greenhouse=greenhouse, lever=lever, smartrecruiters=smartrecruiters, workday=workday, amazon=amazon)

# ---- filtering -----------------------------------------------------------------------------------
def experience(desc):
    lows = []
    for m in re.finditer(r"(\d{1,2})\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years|yrs)", desc.lower()):
        lows.append(int(m[1]))
    if not lows:
        return None, "Not stated"
    lo = min(lows)
    return lo, f"{lo}+ yrs"

def keep(j):
    if not TITLE_OK.search(j["title"]) or TITLE_BAD.search(j["title"]):
        return False
    if not INDIA.search(j["location"]):
        return False
    lo, _ = experience(j["desc"])
    return lo is None or lo <= 3

def process(c):
    status = dict(name=c["name"], type=c["type"], ats=c["ats"], url=c["url"], status="link-only", count=0, error="")
    if c["ats"] not in ADAPTERS:
        return status, []
    try:
        raw = ADAPTERS[c["ats"]](c)
    except Exception as e:  # a broken scraper must never look like "no jobs"
        status.update(status="failed", error=str(e)[:160])
        return status, None
    jobs = []
    for j in raw:
        if not keep(j):
            continue
        low = (j["title"] + " " + j["desc"])
        _, exp = experience(j["desc"])
        city = next((n for n, rx in CITIES.items() if re.search(rx, j["location"], re.I)), "")
        jobs.append(dict(id=hashlib.sha1((c["name"] + j["url"]).encode()).hexdigest()[:12], company=c["name"],
                         type=c["type"], title=j["title"], location=j["location"] or "India", city=city, exp=exp,
                         url=j["url"], ext_id=j["ext_id"], desc=j["desc"][:1500],
                         keywords=[k for k in KEYWORDS if re.search(r"(?<![\w.])" + re.escape(k) + r"(?![\w])", low, re.I)]))
    status.update(status="ok", count=len(jobs))
    return status, jobs

def load_companies():
    out = []
    for line in (ROOT / "companies.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            n, t, a, s, u = [p.strip() for p in line.split("|")]
            out.append(dict(name=n, type=t, ats=a, slug=s, url=u))
    return out

def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prev = json.loads(OUT.read_text()) if OUT.exists() else {"jobs": []}
    prev_jobs = {j["id"]: j for j in prev["jobs"]}
    companies = load_companies()
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(process, companies))
    jobs, statuses = [], []
    for st, found in results:
        st["checked_at"] = now
        if found is None:  # failed: keep last known jobs for this company
            found = [j for j in prev_jobs.values() if j["company"] == st["name"]]
            st["count"] = len(found)
        for j in found:
            j["first_seen"] = prev_jobs.get(j["id"], {}).get("first_seen", now)
            jobs.append(j)
        statuses.append(st)
    jobs.sort(key=lambda j: j["first_seen"], reverse=True)
    OUT.write_text(json.dumps(dict(generated_at=now, jobs=jobs, companies=statuses), ensure_ascii=False, indent=1))
    ok = sum(s["status"] == "ok" for s in statuses)
    print(f"{len(jobs)} jobs | {ok} scraped ok | {sum(s['status']=='failed' for s in statuses)} failed | "
          f"{sum(s['status']=='link-only' for s in statuses)} link-only")

if __name__ == "__main__":
    main()
