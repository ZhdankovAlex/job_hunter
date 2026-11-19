"""Boards and career sites from jobhunter/sites.py: response parsing, pagination, errors. No network."""
import html
import json
import logging

from conftest import FakeHttp, RouteHttp

from jobhunter import sites


def _next_f(payload: str) -> str:
    """A Next.js page: the data stream in chunks of self.__next_f.push([1,"…"])."""
    escaped = json.dumps(payload)[1:-1]
    half = len(escaped) // 2
    while escaped[half - 1] == "\\":  # do not cut in the middle of an escape
        half += 1
    return "".join(f'<script>self.__next_f.push([1,"{part}"])</script>' for part in (escaped[:half], escaped[half:]))


def _ld(data: dict) -> str:
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


# --- fetch: what was collected before an error is kept --------------------------------------

def _rivian_page(n: int, total: int = 3) -> str:
    return json.dumps({"totalCount": total, "jobs": [
        {"data": {"title": f"Backend Engineer {n}", "city": "Palo Alto", "country": "US", "slug": str(n),
                  "description": "<p>Python</p>", "posted_date": "2026-11-10T00:00:00Z"}}]})


def test_rivian_reads_pages_until_total():
    http = RouteHttp(lambda m, url, p: _rivian_page(p["page"]))
    jobs = sites.fetch("rivian", http)
    assert [j.title for j in jobs] == ["Backend Engineer 1", "Backend Engineer 2", "Backend Engineer 3"]
    assert jobs[0].url == "https://careers.rivian.com/careers-home/jobs/1" and jobs[0].location == "Palo Alto, US"
    assert len(http.calls) == 3


def test_fetch_keeps_jobs_when_later_page_is_not_json(caplog):
    """Page 2 came as an HTML stub with code 200: r.json() raised ValueError past fetch(),
    and the run lost the first page already collected (main.py logged an "unexpected error")."""
    http = RouteHttp(lambda m, url, p: _rivian_page(1) if p["page"] == 1 else "<html>maintenance</html>")
    with caplog.at_level(logging.WARNING):
        jobs = sites.fetch("rivian", http)
    assert [j.title for j in jobs] == ["Backend Engineer 1"]
    assert "rivian" in caplog.text and "could not parse" in caplog.text


def test_fetch_logs_http_error_and_returns_empty(caplog):
    with caplog.at_level(logging.WARNING):
        assert sites.fetch("rivian", RouteHttp(lambda *a: (503, ""))) == []
    assert "HTTP 503" in caplog.text


def test_paychex_skips_unsuitable_titles():
    data = {"totalCount": 2, "jobs": [
        {"data": {"title": "Senior Python Developer", "req_id": "42", "city": "Rochester", "country": "US",
                  "description": "<p>Django</p>", "qualifications": "<p>5 years</p>", "employment_type": "FULL_TIME"}},
        {"data": {"title": "Sales Representative", "req_id": "43"}},
    ]}
    jobs = list(sites.paychex(RouteHttp(lambda *a: json.dumps(data))))
    assert len(jobs) == 1
    job = jobs[0]
    assert job.url == "https://careers.paychex.com/careers/jobs/42?lang=en-us"
    assert job.location == "Rochester, US" and job.employment == "full time"
    assert "Django" in job.description and "5 years" in job.description


# --- Aiven: React Router turbo-stream --------------------------------------------------------

def test_turbo_stream_resolves_references():
    # {"routes/careers/job": {"data": {"jobs": [{"title": "Backend Engineer"}], "x": undefined}}}
    raw = json.dumps([{"_1": 2}, "routes/careers/job", {"_3": 4}, "data", {"_5": 6, "_9": -5},
                      "jobs", [7], {"_8": 10}, "title", "x", "Backend Engineer"])
    assert sites._turbo_stream(raw + "\nP1:[]") == {
        "routes/careers/job": {"data": {"jobs": [{"title": "Backend Engineer"}], "x": None}}}


def test_aiven_reads_jobs_and_reports_changed_format(caplog):
    raw = json.dumps([{"_1": 2}, "routes/careers/job", {"_3": 4}, "data", {"_5": 6}, "jobs", [7, 11],
                      {"_8": 9, "_10": 12, "_13": 14}, "title", "Senior Python Engineer", "url",
                      {"_8": 15, "_10": 16}, "https://careers.aiven.io/openings/1", "location", "Remote, Europe",
                      "Account Executive", "https://careers.aiven.io/openings/2"])
    http = FakeHttp({"https://aiven.io/careers/job.data": raw,
                     "https://careers.aiven.io/openings/1": "<nav>menu</nav><main>Kafka and Python</main>"})
    jobs = sites.fetch("aiven", http)
    assert [(j.title, j.remote) for j in jobs] == [("Senior Python Engineer", True)]
    assert jobs[0].description == "Kafka and Python"
    assert "https://careers.aiven.io/openings/2" not in http.calls  # wrong role: the page is not opened

    with caplog.at_level(logging.WARNING):
        assert sites.fetch("aiven", FakeHttp({"https://aiven.io/careers/job.data": "[{}]"})) == []
    assert "job.data format changed" in caplog.text


# --- Habr Career: SSR JSON (a Russian-language site, so the data is in Russian) ---------------

def _habr_page(items: list, total_pages: int = 1) -> str:
    data = {"vacancies": {"list": items, "meta": {"totalPages": total_pages}}}
    return f'<script type="application/json" data-ssr-state="true">{html.escape(json.dumps(data))}</script>'


def test_habr_career_reads_list_and_description():
    items = [
        {"title": "Python-разработчик", "href": "/vacancies/1", "remoteWork": True,
         "company": {"title": "Acme"}, "skills": [{"title": "Django"}],
         "salary": {"formatted": "от 300 000 ₽"}, "publishedDate": {"date": "2026-11-10T10:00:00+03:00"},
         "employment": "full_time"},
        {"title": "Python-разработчик (архив)", "href": "/vacancies/2", "archived": True},
        {"title": "Sales Manager", "href": "/vacancies/3"},
    ]
    detail = '<div class="vacancy-description__text">We write <b>FastAPI</b></div><div class="basic-section">x</div>'
    http = RouteHttp(lambda m, url, p: _habr_page(items) if "/vacancies?" in url
                     else detail if url.endswith("/vacancies/1") else None)
    jobs = list(sites.habr_career(http))
    assert len(jobs) == 1
    job = jobs[0]
    assert (job.company, job.location, job.salary, job.employment) == ("Acme", "Remote", "от 300 000 ₽", "full-time")
    assert job.description == "We write FastAPI\nSkills: Django"  # no trailing '<div class="' of the next section
    assert len(http.calls) == 2  # one list page (totalPages=1) and one vacancy


def test_habr_career_without_ssr_is_fetch_error(caplog):
    with caplog.at_level(logging.WARNING):
        assert sites.fetch("habr career", RouteHttp(lambda *a: "<html></html>")) == []
    assert "no SSR data" in caplog.text


# --- relocate.me, 01.tech, hirehi: links and JSON-LD -----------------------------------------

def test_relocate_me_parses_link_and_stops_on_repeated_page():
    listing = ('<a class="job" href="/portugal/lisbon/acme-games/senior-python-developer-123">'
               "Senior Python Developer in Lisbon</a>"
               '<a href="/remote/remote/globex/sales-manager-124">Sales Manager</a>')
    http = RouteHttp(lambda m, url, p: listing if "international-jobs" in url else "<main>Visa support</main>")
    jobs = list(sites.relocate_me(http))
    assert [(j.title, j.company, j.location, j.remote) for j in jobs] == [
        ("Senior Python Developer", "Acme Games", "Lisbon, Portugal", False)]
    assert sum("international-jobs" in url for _, url, _ in http.calls) == 2  # page 2 has the same links


def test_zero_one_tech_opens_ids_and_reads_format():
    ids = ["a" * 24, "b" * 24, "c" * 24]
    pages = {
        "https://01.tech/vacancies": "<script>" + json.dumps({"ids": ids}) + "</script>",
        f"https://01.tech/vacancies/{ids[0]}": "<title>Vacancy: Python Developer</title><main>Remote · Full-time</main>",
        f"https://01.tech/vacancies/{ids[1]}": "<title>Vacancy: HR Manager</title>",
    }  # the third id is missing (404): not a vacancy, skipped
    jobs = list(sites.zero_one_tech(FakeHttp(pages)))
    assert [(j.title, j.location, j.remote) for j in jobs] == [("Python Developer", "Remote", True)]


def test_hirehi_reads_itemlist_and_jobposting():
    url = "https://hirehi.ru/vacancy/1"
    listing = _ld({"@type": "ItemList", "itemListElement": [
        {"url": url, "name": "Python Developer"}, {"url": "https://hirehi.ru/vacancy/2", "name": "Designer"}]})
    posting = _ld({"@type": "JobPosting", "title": "Python Developer", "hiringOrganization": {"name": "Acme"},
                   "jobLocationType": "TELECOMMUTE", "description": "&lt;p&gt;FastAPI&lt;/p&gt;",
                   "baseSalary": {"currency": "RUB", "value": {"value": 300000, "unitText": "MONTH"}},
                   "employmentType": "FULL_TIME", "datePosted": "2026-11-10"})
    pages = {"https://hirehi.ru/vacancies/python": listing, "https://hirehi.ru/vacancies/backend": listing,
             url: posting}
    http = FakeHttp(pages)
    jobs = list(sites.hirehi(http))
    assert len(jobs) == 1  # the same vacancy in the second section is not duplicated
    job = jobs[0]
    assert (job.company, job.location, job.remote, job.salary) == ("Acme", "Remote", True, "300000 RUB")
    assert job.description == "FastAPI" and job.employment == "full-time"
    assert http.calls.count(url) == 1


# --- RSS and Next.js -------------------------------------------------------------------------

def test_cryptocurrencyjobs_rss():
    rss = """<rss><channel>
      <item><title>Senior Backend Engineer at Acme Labs</title><link>https://cryptocurrencyjobs.co/a/</link>
        <description>Work from anywhere. Part-time position.</description>
        <pubDate>Mon, 10 Nov 2026 10:00:00 +0000</pubDate></item>
      <item><title>Rust Developer at Globex</title><link>https://cryptocurrencyjobs.co/b/</link>
        <description>Remote in Europe. Full-time.</description><pubDate>not a date</pubDate></item>
      <item><title>Office Manager</title><description>Lisbon office.</description></item>
    </channel></rss>"""
    jobs = list(sites.cryptocurrencyjobs(FakeHttp({"https://cryptocurrencyjobs.co/index.xml": rss})))
    assert [(j.company, j.title, j.location, j.employment) for j in jobs] == [
        ("Acme Labs", "Senior Backend Engineer", "Remote, Worldwide", "part-time"),
        ("Globex", "Rust Developer", "Remote - Europe", ""),
        ("", "Office Manager", "", ""),  # without " at " the title is not the company
    ]
    assert jobs[0].posted is not None and jobs[1].posted is None and jobs[2].remote is None


def test_cryptocurrencyjobs_broken_rss_is_fetch_error(caplog):
    with caplog.at_level(logging.WARNING):
        assert sites.fetch("cryptocurrencyjobs", FakeHttp({"https://cryptocurrencyjobs.co/index.xml": "<rss>"})) == []
    assert "could not parse RSS" in caplog.text


def test_justjoin_reads_offers_from_next_payload(caplog):
    url = sites._boards()["justjoin_url"]
    offers = [{"applyUrl": None, "title": "Python Developer", "companyName": "Acme", "slug": "acme-python",
               "city": "Warszawa", "multilocation": [{"city": "Warszawa"}, {"city": "Kraków"}],
               "workplaceType": "remote", "requiredSkills": ["Python"], "niceToHaveSkills": ["AWS"],
               "workingTime": "full_time", "publishedAt": "2026-11-10T10:00:00Z"}]
    page = _next_f('0:["$","div"]\n5:{"offers":' + json.dumps(offers) + ',"count":1}')
    jobs = list(sites.justjoin(FakeHttp({url: page})))
    assert [(j.title, j.location, j.remote) for j in jobs] == [
        ("Python Developer", "Warszawa, Kraków, Poland (remote)", True)]
    assert jobs[0].url == "https://justjoin.it/job-offer/acme-python" and "Skills: Python, AWS" in jobs[0].description

    with caplog.at_level(logging.WARNING):  # the page format changed: loudly, not "0 vacancies"
        assert sites.fetch("justjoin.it", FakeHttp({url: "<html></html>"})) == []
    assert "page format changed" in caplog.text


def test_remotecom_pages_until_empty():
    jobs_json = [{"status": "active", "title": "Backend Engineer", "slug": "be",
                  "companyProfile": {"name": "Acme", "slug": "acme"},
                  "workplaceLocation": {"type": "remote"}, "visaSponsorshipOffered": True,
                  "hiringLocation": {"includedLocations": [{"value": {"name": "Portugal"}}]}}]
    first = _next_f('1:{"jobs":' + json.dumps(jobs_json) + "}")
    http = RouteHttp(lambda m, url, p: first if "page=" not in url else _next_f('1:{"jobs":[]}'))
    jobs = list(sites.remotecom(http))
    assert [(j.title, j.location, j.url) for j in jobs] == [
        ("Backend Engineer", "Remote: Portugal", "https://remote.com/jobs/acme/be")]
    assert "visa sponsorship" in jobs[0].description and len(http.calls) == 2


def test_next_payload_and_array_after_tolerate_garbage():
    assert sites._next_payload("<html></html>") == ""
    assert sites._array_after('"offers":[{"applyUrl"', '"offers":[{"applyUrl"') == []  # truncated JSON
    assert sites._array_after("no marker", '"jobs":') == []


def test_remoterocketship_next_data():
    base = sites._boards()["remoterocketship_url"]
    openings = [{"roleTitle": "Senior Python Engineer", "company": {"name": "Acme"}, "url": "https://ats.example.com/1",
                 "locationType": "remote", "location": "Europe", "locationCountries": ["Portugal", "Poland"],
                 "techStack": ["Python", "AWS"], "salaryRange": "$100k-$120k", "roleDescription": "<p>APIs</p>"}]

    def page(data: dict) -> str:
        return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'

    pages = {f"{base}&page=1": page({"props": {"pageProps": {"initialJobOpenings": openings}}}),
             f"{base}&page=2": page({"props": {"pageProps": {"initialJobOpenings": []}}})}
    jobs = list(sites.remoterocketship(FakeHttp(pages)))
    assert [(j.company, j.location, j.salary, j.remote) for j in jobs] == [
        ("Acme", "Europe; Portugal; Poland (remote)", "$100k-$120k", True)]
    assert "APIs" in jobs[0].description and "Skills: Python, AWS" in jobs[0].description


# --- manual_jobs: JSON-LD, Next.js data, <title> ---------------------------------------------

def test_manual_jobs_three_ways_to_read_page(monkeypatch, caplog):
    from jobhunter import geo

    urls = ["https://jobs.acme.com/1", "https://www.globex.com/careers/position/2",
            {"url": "https://withthrive.com/3", "company": "Thrive", "location": "Remote"},
            "https://gone.example.com/4"]
    monkeypatch.setitem(geo.S, "manual_jobs", {"urls": urls})
    next_data = {"props": {"pageProps": {"position": {
        "text": "Backend Engineer", "description": "<p>Go</p>",
        "locations": [{"name": "Lisbon", "type": "office"}, {"name": "Remote - Europe", "type": "remote"}]}}}}
    pages = {
        "https://jobs.acme.com/1": _ld({"@type": "JobPosting", "title": "Python Developer",
                                         "hiringOrganization": {"name": "Acme Corp"},
                                         "jobLocation": {"address": {"addressLocality": "Porto",
                                                                     "addressCountry": "PT"}},
                                         "description": "&lt;b&gt;Django&lt;/b&gt;"}),
        "https://www.globex.com/careers/position/2":
            f'<script id="__NEXT_DATA__">{json.dumps(next_data)}</script>',
        "https://withthrive.com/3": "<title>Lead Engineer - Thrive | Careers</title><main>Python</main>",
    }
    with caplog.at_level(logging.WARNING):
        jobs = list(sites.manual_jobs(FakeHttp(pages)))
    assert [(j.company, j.title, j.location, j.remote) for j in jobs] == [
        ("Acme Corp", "Python Developer", "Porto, PT", None),
        ("Globex", "Backend Engineer", "Lisbon; Remote - Europe", True),
        ("Thrive", "Lead Engineer", "Remote", True),
    ]
    assert jobs[0].description == "Django" and all(j.source == "manual" for j in jobs)
    assert "gone.example.com" in caplog.text  # an unavailable page is a warning, the rest are read


def test_jsonld_skips_broken_blocks_and_reads_graph():
    page = ('<script type="application/ld+json">{broken</script>'
            + _ld({"@graph": [{"@type": "Organization"}, {"@type": "JobPosting", "title": "X"}]}))
    assert sites._jsonld(page, "JobPosting") == {"@type": "JobPosting", "title": "X"}
    assert sites._jsonld(page, "ItemList") == {}


def test_bolt_pages_dedupe_and_details():
    """Bolt: pages until one has no new vacancies, repeats are skipped, the text only for suitable titles."""
    base = "https://bolt.eu/en/careers/positions/"
    ids = ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]

    def item(uid, title, place):
        return (f'<div data-testid="AccordionItem"><a href="/en/careers/positions/{uid}"><style>.x{{}}</style>'
                f"<span>{title}</span><span>Engineering</span><span>{place}</span></a></div>")

    http = FakeHttp({
        base: item(ids[0], "Senior Backend Engineer", "Tallinn") + item(ids[1], "Office Manager", "Tallinn"),
        base + "?page=2": item(ids[0], "Senior Backend Engineer", "Tallinn"),  # only a repeat: stop paging
        f"{base}{ids[0]}/": "<p>About the role. Python.</p><p>WORKPLACE Remote</p><p>TYPE Full-time View all jobs</p>",
    })
    (job,) = sites.bolt(http)
    assert (job.title, job.location, job.remote, job.employment) == (
        "Senior Backend Engineer", "Tallinn (Remote)", True, "Full-time")
    assert f"{base}{ids[1]}/" not in http.calls and base + "?page=3" not in http.calls
