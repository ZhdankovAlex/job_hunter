"""Application status and funnel: a repeated process, cancelled meetings, "declined by you", days of silence."""
from jobhunter import applications_report as ar, mail
from jobhunter.mail import APPLIED, DECLINED, INTERVIEW, OTHER, REJECTED, Application, Event


def app(*events):
    """app((-30, APPLIED, "subject"), …) — events by day relative to the test's "now"."""
    # thread — as for a letter from mail (events from applications_manual.toml have none)
    return lambda at: Application("Acme", [Event(at(d), k, s, "hr@acme.com", "t1") for d, k, s in events])


def status(make, at, now):
    return ar.effective_status(make(at), "", now)[0]


def test_reapplied_with_new_stages_is_active(at, now):
    """Rejected in summer, applied again, then an HR interview — stages are going on, not "waiting for an answer"."""
    make = app((-130, APPLIED, "Applied"), (-120, REJECTED, "Rejected"),
               (-2, APPLIED, "The recruiter wrote again"), (-1, INTERVIEW, "HR interview"))
    assert status(make, at, now) == ar.ACTIVE
    assert ar.effective_status(make(at), "", now)[1] == "applied again after a rejection"


def test_old_stages_before_reapply_do_not_count(at, now):
    """Stages happened before the new application (for another vacancy) — the new process is still waiting for an answer."""
    make = app((-140, APPLIED, "Applied"), (-137, REJECTED, "Application update"),
               (-128, INTERVIEW, "Select availability for Screen Call"),
               (-120, INTERVIEW, "Interview cancelled - Screen Call"),
               (-17, APPLIED, "Application confirmed"), (-6, APPLIED, "Application confirmed"))
    assert status(make, at, now) == ar.WAITING


def test_cancelled_series_is_not_a_stage(at):
    """Pick a slot → confirmation → reminder → cancellation: the stage did not happen."""
    make = app((-10, APPLIED, "Applied"), (-8, INTERVIEW, "Select availability for Screen Call"),
               (-7, INTERVIEW, "Confirmation - Screen Call"), (-5, INTERVIEW, "Interview cancelled - Screen Call"))
    assert ar.funnel_stage(make(at)) == "applied"


def test_stage_after_cancelled_series_counts(at):
    """One meeting was cancelled, a month later there was a real technical stage — it counts."""
    make = app((-60, APPLIED, "Applied"), (-55, INTERVIEW, "Screen Call"), (-54, INTERVIEW, "Meeting cancelled"),
               (-10, INTERVIEW, "Technical interview"))
    assert ar.funnel_stage(make(at)) == ar.TECH


def test_final_by_word_in_note(at):
    make = app((-20, APPLIED, "Applied"), (-5, INTERVIEW, "Booked the final technical interview"))
    assert ar.funnel_stage(make(at)) == ar.FINAL


def test_declined_by_me(at, now):
    make = app((-40, APPLIED, "Applied"), (-30, INTERVIEW, "Tech"), (-1, DECLINED, "Declined"))
    assert status(make, at, now) == ar.DECLINED


def test_silence_turns_into_rejection(at, now):
    make = app((-(ar.TIMEOUT_DAYS + 1), APPLIED, "Applied"))
    assert status(make, at, now) == ar.REJECTED


def test_future_event_gives_zero_silence(at, now):
    """An event written in advance (a scheduled meeting) is not "-1 day of silence" and not a rejection."""
    make = app((-3, APPLIED, "Applied"), (0.2, INTERVIEW, "Final tomorrow"))
    st, why = ar.effective_status(make(at), "", now)
    assert st == ar.ACTIVE
    assert "-" not in why


def test_other_letters_do_not_change_status(at, now):
    make = app((-5, INTERVIEW, "Screen"), (-1, OTHER, "Just correspondence"))
    assert make(at).status == INTERVIEW
    assert status(make, at, now) == ar.ACTIVE


def test_render_history_marks_cancelled_and_hides_silence_for_closed(at, now):
    open_app = app((-10, APPLIED, "Applied"), (-8, INTERVIEW, "Screen Call"), (-7, INTERVIEW, "Interview cancelled"))(at)
    closed = Application("Closed Inc", [Event(at(-50), APPLIED, "Applied", "x", ""),
                                        Event(at(-40), REJECTED, "Rejected", "x", "")])
    page = ar.render(now.date(), [open_app, closed], [], "me@example.com")
    assert "cancelled" in page
    row = page[page.index("Closed Inc"):].split("</tr>")[0]
    assert '<td class="n" data-sort="-1"></td>' in row


def test_manual_kind_declined_is_known():
    assert mail.DECLINED == "declined"
    from jobhunter import manual_apps
    assert "declined" in manual_apps.KINDS


def test_cancellation_after_rejection_keeps_rejection(at, now):
    """A rejection letter, 20 minutes later "Meeting cancelled". This is a rejection, not "no answer after a stage"."""
    make = app((-110, APPLIED, "Applied"), (-108, INTERVIEW, "Meeting invite: First stage interview"),
               (-105, REJECTED, "Update on your application"),
               (-105 + 0.01, INTERVIEW, "Meeting cancelled: First stage interview"))
    assert ar.effective_status(make(at), "", now) == (ar.REJECTED, "rejection in the correspondence")


def test_manual_stage_survives_cancelled_series(at):
    """A screening written by hand stays a stage even if the letters about the meeting ended with a cancellation."""
    a = app((-10, APPLIED, "Applied"), (-8, INTERVIEW, "Meeting invite"), (-5, INTERVIEW, "Meeting cancelled"))(at)
    a.events.append(Event(at(-8), INTERVIEW, "Screening scheduled, the interviewer did not show up", "manual", ""))
    for e in a.events[:3]:
        e.thread = "t1"  # letters from mail have a thread, manual events do not
    assert ar.funnel_stage(a) == ar.SCREEN
