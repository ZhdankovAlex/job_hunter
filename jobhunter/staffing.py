"""Blacklist of staffing agencies / intermediaries from staffing_blacklist.txt."""
import logging
import re

from . import config
from .keys import company_key

log = logging.getLogger(__name__)

# outsourcing / outstaffing by the vacancy text; "our clients" is not included — product B2B companies write it too
OUTSOURCING_TEXT_RX = re.compile(
    r"outstaff\w*|outsourc\w*|staff augmentation|for (?:a|one of our) clients?\b|"
    r"аутстаф\w*|аутсорс\w*|на проект(?:е)? (?:в|для|у) (?:банк|заказчик|клиент)\w*",
    re.I,
)


class Blacklist:
    def __init__(self, path=config.STAFFING_FILE):
        self.names, self.patterns = set(), []
        if not path.exists():
            log.warning("File %s not found — the blacklist is empty", path.name)
            return
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("re:"):
                try:
                    self.patterns.append(re.compile(line[3:].strip(), re.I))
                except re.error as e:
                    log.warning("%s: invalid pattern %r (%s)", path.name, line, e)
            else:
                self.names.add(company_key(line))

    def reason(self, company, title, text=""):
        """Why the vacancy goes to the Staffing appendix, or ''."""
        key = company_key(company)
        # "AcmeTalent Inc. - Globex" — the same intermediary under a longer name
        if key in self.names or any(len(n) >= 6 and key.startswith(n) for n in self.names):
            return "blacklist"
        if any(rx.search(company) for rx in self.patterns):
            return "looks like an intermediary"
        m = OUTSOURCING_TEXT_RX.search(f"{title}\n{text}")
        if m:
            return f"outsourcing by the text: \"{m.group()}\""
        return ""
