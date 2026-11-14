# SDE-1 jobs dashboard (India)
- `companies.txt` – the ~100 companies (edit freely). `ats` says how to read the company's jobs; `link` means "check manually".
- `scrape.py` – filters to SDE-1 style roles, India, about 0-3 years. Writes `docs/jobs.json`.
- `.github/workflows/refresh.yml` – runs the scraper every 12 hours and commits the result.
- `docs/index.html` – the dashboard (served by GitHub Pages).
Local preview: `python scrape.py && cd docs && python -m http.server 8000`
