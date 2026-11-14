"""Collect SDE-1 style jobs (max 2 years' experience) in India -> docs/jobs.json
Per company: 1) override from companies.txt  2) auto-detect the hiring system from the live careers page
3) headless-browser fallback. The dashboard shows which method worked, or the exact error."""
import hashlib, html, json, re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).parent
OUT = ROOT / "docs" / "jobs.json"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.9"}
MAX_EXP = 2  # years

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

def http(method, url, **kw):
    """Request JSON; raise an error that says exactly what came back."""
    headers = {**UA, "Accept": "application/json", **kw.pop("headers", {})}
    r = requests.request(method, url, headers=headers, timeout=30, **kw)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} {r.text[:70]!r}")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"Not JSON: {r.text[:70]!r}")

# ---- adapters: take a list of args, return dicts with title, location, url, desc, ext_id ---------
def greenhouse(a):
    d = http("GET", f"https://boards-api.greenhouse.io/v1/boards/{a[0]}/jobs?content=true")
    return [dict(title=j["title"], location=(j.get("location") or {}).get("name", ""), url=j["absolute_url"],
                 desc=clean(j.get("content")), ext_id=str(j["id"])) for j in d.get("jobs", [])]

def lever(a):
    return [dict(title=j["text"], location=(j.get("categories") or {}).get("location") or "", url=j["hostedUrl"],
                 desc=j.get("descriptionPlain") or "", ext_id=j["id"])
            for j in http("GET", f"https://api.lever.co/v0/postings/{a[0]}?mode=json")]

def ashby(a):
    d = http("GET", f"https://api.ashbyhq.com/posting-api/job-board/{a[0]}")
    return [dict(title=j["title"], location=", ".join([j.get("location") or ""] + [s.get("location", "") if isinstance(s, dict) else str(s) for s in j.get("secondaryLocations") or []]),
                 url=j["jobUrl"], desc=j.get("descriptionPlain") or "", ext_id=str(j.get("id", j["jobUrl"]))) for j in d.get("jobs", [])]

def workable(a):
    d = http("POST", f"https://apply.workable.com/api/v3/accounts/{a[0]}/jobs",
             json={"query": "software engineer", "location": [], "department": [], "worktype": [], "remote": []})
    return [dict(title=j["title"], location=", ".join(x for x in [(j.get("location") or {}).get("city"), (j.get("location") or {}).get("country")] if x),
                 url=f"https://apply.workable.com/{a[0]}/j/{j['shortcode']}/", desc="", ext_id=j["shortcode"]) for j in d.get("results", [])]

def smartrecruiters(a):
    d = http("GET", f"https://api.smartrecruiters.com/v1/companies/{a[0]}/postings", params=dict(country="in", q="software", limit=100))
    return [dict(title=j["name"], location=f"{j['location'].get('city', '')}, India", desc="", ext_id=j["id"],
                 url=f"https://jobs.smartrecruiters.com/{a[0]}/{j['id']}") for j in d.get("content", [])]

def workday(a):
    tenant, wd, site = a[:3]
    base, out = f"https://{tenant}.{wd}.myworkdayjobs.com", []
    hdr = {"Content-Type": "application/json", "Origin": base, "Referer": f"{base}/{site}"}
    for off in range(0, 100, 20):
        d = http("POST", f"{base}/wday/cxs/{tenant}/{site}/jobs", headers=hdr,
                 json={"appliedFacets": {}, "limit": 20, "offset": off, "searchText": "software engineer india"})
        posts = d.get("jobPostings", [])
        if not posts:
            break
        out += [dict(title=p["title"], location=p.get("locationsText", ""), desc="", ext_id=p["externalPath"],
                     url=f"{base}/{site}{p['externalPath']}") for p in posts]
    return out

def eightfold(a):
    host = a[0]
    domain = a[1] if len(a) > 1 and a[1] else host.split(".")[0] + ".com"
    out = []
    for s in range(0, 100, 10):
        d = http("GET", f"https://{host}/api/apply/v2/jobs", params=dict(domain=domain, start=s, num=10, query="software engineer", location="India"))
        ps = d.get("positions", [])
        if not ps:
            break
        out += [dict(title=p["name"], location=p.get("location") or ", ".join(p.get("locations") or []),
                     url=p.get("canonicalPositionUrl") or f"https://{host}/careers/job/{p['id']}",
                     desc=clean(p.get("job_description")), ext_id=str(p["id"])) for p in ps]
    return out

def oracle(a):
    host, site, out = a[0], a[1], []
    for off in range(0, 200, 50):
        d = http("GET", f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions", params={
            "onlyData": "true", "expand": "requisitionList.secondaryLocations",
            "finder": f'findReqs;siteNumber={site},keyword="software engineer india",limit=50,offset={off},sortBy=POSTING_DATES_DESC'})
        reqs = ((d.get("items") or [{}])[0]).get("requisitionList", [])
        if not reqs:
            break
        out += [dict(title=r["Title"], location=r.get("PrimaryLocation", ""), desc=clean(r.get("ShortDescriptionStr")), ext_id=str(r["Id"]),
                     url=f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{r['Id']}") for r in reqs]
    return out

def microsoft(a):
    out = []
    for pg in range(1, 6):
        d = http("GET", "https://gcsservices.careers.microsoft.com/search/api/v1/search",
                 params=dict(lc="India", q="software engineer", l="en_us", pg=pg, pgSz=20, o="Relevance", flt="true"))
        jobs = d["operationResult"]["result"]["jobs"]
        if not jobs:
            break
        out += [dict(title=j["title"], location=", ".join((j.get("properties") or {}).get("locations") or ["India"]),
                     desc=clean((j.get("properties") or {}).get("description")), ext_id=str(j["jobId"]),
                     url=f"https://jobs.careers.microsoft.com/global/en/job/{j['jobId']}") for j in jobs]
    return out

def atlassian(a):
    return [dict(title=j["title"], location=", ".join(j.get("locations") or []), desc="", ext_id=str(j["id"]),
                 url=j.get("applyUrl") or f"https://www.atlassian.com/company/careers/details/{j['id']}")
            for j in http("GET", "https://www.atlassian.com/endpoint/careers/listings")]

def amazon(a):
    d = http("GET", "https://www.amazon.jobs/en/search.json", params={
        "base_query": "software development engineer", "loc_query": "India", "country": "IND", "result_limit": 100})
    return [dict(title=j["title"], location=j.get("location", "India"), url="https://www.amazon.jobs" + j["job_path"],
                 desc=clean(j.get("basic_qualifications", "") + " " + j.get("description_short", "")),
                 ext_id=j.get("id_icims") or j["job_path"]) for j in d.get("jobs", [])]

ADAPTERS = dict(greenhouse=greenhouse, lever=lever, ashby=ashby, workable=workable, smartrecruiters=smartrecruiters,
                workday=workday, eightfold=eightfold, oracle=oracle, microsoft=microsoft, atlassian=atlassian, amazon=amazon)

# ---- detecting which hiring system a careers page uses ---------------------------------------------
PATTERNS = [
    ("greenhouse", r"greenhouse\.io/embed/job_board(?:/js)?\?for=([\w-]+)"),
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/([\w-]+)"),
    ("lever", r"jobs\.(?:eu\.)?lever\.co/([\w.-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([\w.%-]+)"),
    ("workable", r"apply\.workable\.com/([\w-]+)"),
    ("smartrecruiters", r"(?:careers|jobs)\.smartrecruiters\.com/([\w-]+)"),
    ("workday", r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/wday/cxs/[\w-]+/([\w-]+)"),
    ("workday", r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Za-z]{2}/)?([\w-]+)"),
    ("oracle", r"([\w-]+\.fa\.(?:[\w-]+\.)*oraclecloud\.com)/hcmUI/CandidateExperience/\w+/sites/([\w-]+)"),
    ("eightfold", r"([\w-]+\.eightfold\.ai)"),
]
OTHER = [("iCIMS", r"icims\.com"), ("SuccessFactors", r"successfactors|jobs\.sap\.com"), ("Taleo", r"taleo\.net"),
         ("Phenom", r"phenompeople"), ("Radancy", r"tmpwebeng|radancy"), ("Zoho Recruit", r"zohorecruit"),
         ("Freshteam", r"freshteam"), ("Jobvite", r"jobvite")]
SKIP = {"embed", "js", "api", "v1", "sitemap", "static", "assets", "wday", "job", "jobs", "www", "careers"}

def detect(text):
    for name, rx in PATTERNS:
        for m in re.finditer(rx, text):
            g = list(m.groups())
            if any(x.lower() in SKIP for x in g):
                continue
            if name == "eightfold":
                d = re.search(r"domain=([\w-]+\.\w+)", text)
                g.append(d.group(1) if d else "")
            return name, g
    for label, rx in OTHER:
        if re.search(rx, text, re.I):
            return label, None
    return None, None

# ---- filtering and building jobs --------------------------------------------------------------------
def experience(desc):
    lows = [int(m[1]) for m in re.finditer(r"(\d{1,2})\s*(-|–|to)?\s*(\d{1,2})?\s*\+?\s*(years|yrs)", desc.lower())]
    return (None, "Not stated") if not lows else (min(lows), f"{min(lows)}+ yrs")

def keep(j):
    t = j["title"]
    if not TITLE_OK.search(t) or TITLE_BAD.search(t) or not INDIA.search(j["location"]):
        return False
    lo, _ = experience(j["desc"])
    return lo is None or lo <= MAX_EXP

def make_jobs(c, raw):
    jobs = []
    for j in raw:
        if not keep(j):
            continue
        low = j["title"] + " " + j["desc"]
        city = next((n for n, rx in CITIES.items() if re.search(rx, j["location"], re.I)), "")
        jobs.append(dict(id=hashlib.sha1((c["name"] + j["url"]).encode()).hexdigest()[:12], company=c["name"], type=c["type"],
                         title=j["title"], location=j["location"] or "India", city=city, exp=experience(j["desc"])[1],
                         url=j["url"], ext_id=j["ext_id"], desc=j["desc"][:1500],
                         keywords=[k for k in KEYWORDS if re.search(r"(?<![\w.])" + re.escape(k) + r"(?![\w])", low, re.I)]))
    return jobs

# ---- per-company pipeline ---------------------------------------------------------------------------
def try_adapter(name, args, errors):
    try:
        return ADAPTERS[name](args)
    except Exception as e:
        errors.append(f"{name}: {str(e)[:110]}")
        return None

def process(c):
    st = dict(name=c["name"], type=c["type"], url=c["url"], system="", method="", status="pending", count=0, error="")
    errors, raw = [], None
    if c["override"]:
        name, *args = c["override"].split(":")
        if name in ADAPTERS:
            raw = try_adapter(name, args, errors)
            if raw is not None:
                st.update(system=name, method="feed")
    if raw is None:
        text = ""
        try:
            r = requests.get(c["url"], headers=UA, timeout=30)
            text = r.url + "\n" + r.text
        except Exception as e:
            errors.append(f"page: {str(e)[:80]}")
        name, args = detect(text)
        if name in ADAPTERS:
            raw = try_adapter(name, args, errors)
            if raw is not None:
                st.update(system=name, method="feed")
        elif name:
            st["system"] = name
    st["error"] = " | ".join(errors)[:260]
    if raw is None:
        return st, None
    st.update(status="ok")
    jobs = make_jobs(c, raw)
    st["count"] = len(jobs)
    return st, jobs

# ---- headless-browser fallback (sites with no public feed) -----------------------------------------
JS_LINKS = """() => [...document.querySelectorAll('a[href]')].map(a => {
  const text = (a.innerText || a.getAttribute('aria-label') || '').trim(); let n = a, ctx = text;
  for (let i = 0; i < 4 && n.parentElement; i++) { n = n.parentElement; const t = (n.innerText || '').trim(); if (t.length > 400) break; ctx = t; }
  return {text: text.slice(0, 200), href: a.href, ctx: ctx};
})"""

def browse(ctx, c):
    page, seen = ctx.new_page(), []
    page.on("request", lambda r: seen.append(r.url))
    try:
        page.goto(c["url"], wait_until="domcontentloaded", timeout=35000)
        page.wait_for_timeout(6000)
        for _ in range(4):
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(700)
        blob = page.url + "\n" + "\n".join(seen) + "\n" + page.content()
        links, url = page.evaluate(JS_LINKS), page.url
    finally:
        page.close()
    name, args = detect(blob)
    errs = []
    if name in ADAPTERS:
        raw = try_adapter(name, args, errs)
        if raw is not None:
            return name, "feed", raw, ""
    if len(links) < 10:
        raise RuntimeError("page showed no job list (needs a custom scraper) " + " ".join(errs))
    raw, seen_href = [], set()
    for l in links:
        title = (l["text"].split("\n")[0]).strip()
        if l["href"] in seen_href or not title or len(title) > 120:
            continue
        seen_href.add(l["href"])
        loc = l["ctx"] if INDIA.search(l["ctx"]) else ("India" if "india" in url.lower() else "")
        raw.append(dict(title=title, location=loc, url=l["href"], desc="", ext_id=l["href"][-40:]))
    return name or "page", "page", raw, ""

def browser_stage(pending):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        for st, _ in pending:
            st.update(status="failed", error=(st["error"] + " | browser fallback not installed")[:260])
        return {}
    found = {}
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:
            for st, _ in pending:
                st.update(status="failed", error=(st["error"] + " | browser unavailable: " + str(e)[:80])[:260])
            return {}
        ctx = browser.new_context(user_agent=UA["User-Agent"], locale="en-IN", viewport={"width": 1366, "height": 900})
        for st, c in pending:
            try:
                name, method, raw, _ = browse(ctx, c)
                jobs = make_jobs(c, raw)
                st.update(status="ok", method=method, system=name if name and name != "page" else st["system"], count=len(jobs), error="")
                found[c["name"]] = jobs
            except Exception as e:
                st.update(status="failed", error=(st["error"] + " | browser: " + str(e))[:260])
        browser.close()
    return found

# ---- main -------------------------------------------------------------------------------------------
def load_companies():
    out = []
    for line in (ROOT / "companies.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            p = [x.strip() for x in line.split("|")] + [""]
            out.append(dict(name=p[0], type=p[1], url=p[2], override=p[3]))
    return out

def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prev = json.loads(OUT.read_text()) if OUT.exists() else {"jobs": []}
    prev_jobs = {j["id"]: j for j in prev["jobs"]}
    companies = load_companies()
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(process, companies))
    pending = [(st, c) for (st, jobs), c in zip(results, companies) if jobs is None]
    browser_jobs = browser_stage(pending) if pending else {}
    jobs, statuses = [], []
    for st, found in results:
        st["checked_at"] = now
        if found is None:
            found = browser_jobs.get(st["name"])
        if found is None:  # failed: keep last known jobs so a broken scraper never looks like "no jobs"
            found = [j for j in prev_jobs.values() if j["company"] == st["name"]]
            st["count"] = len(found)
        for j in found:
            j["first_seen"] = prev_jobs.get(j["id"], {}).get("first_seen", now)
            jobs.append(j)
        statuses.append(st)
    jobs.sort(key=lambda j: j["first_seen"], reverse=True)
    OUT.write_text(json.dumps(dict(generated_at=now, jobs=jobs, companies=statuses), ensure_ascii=False, indent=1))
    n = lambda s: sum(x["status"] == s for x in statuses)
    print(f"{len(jobs)} jobs | {n('ok')} companies read | {n('failed')} failed")
    for s in statuses:
        if s["status"] == "failed":
            print("FAILED", s["name"], "-", s["error"])

if __name__ == "__main__":
    main()
