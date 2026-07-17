"""Seeds a full demo campaign for admin@focalreach.com that exercises every state,
failure case, and edge case in the pipeline — so the whole application's behavior can
be verified from the UI without waiting for real prospects.

Idempotent: re-running deletes the previous demo campaign (and everything cascaded
under it) and recreates it fresh.

    python scripts/seed_demo_campaign.py

Login afterward: admin@focalreach.com / Admin@123
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.crypto import encrypt_secret
from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.campaign import Campaign
from app.models.company import Company, QualificationStatus
from app.models.company_intelligence import CompanyIntelligence
from app.models.email_draft import (
    STEP_INITIAL,
    STEP_SCHEDULING_REPLY,
    STEP_SLOT_ALTERNATIVES,
    DispatchLog,
    DraftStatus,
    EmailDraft,
)
from app.models.icp import ICP
from app.models.inbound_reply import InboundReply, ReplyIntent
from app.models.lead import Lead, LeadTier
from app.models.lead_import import ImportStatus, LeadImport
from app.models.mailbox_connection import MailboxConnection, MailboxProvider
from app.models.notification import Notification
from app.models.organization import Organization
from app.models.pending_booking import PendingBooking, PendingBookingStatus, TimezoneSource
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.models.website_analysis import AnalysisStatus, WebsiteAnalysis

UTC = timezone.utc
NOW = datetime.now(UTC)
DEMO_EMAIL = "admin@focalreach.com"
DEMO_PASSWORD = "Admin@123"
CAMPAIGN_NAME = "Demo — Full Pipeline Showcase"


def get_or_create(db, model, defaults=None, **filters):
    obj = db.scalars(select(model).filter_by(**filters)).first()
    if obj:
        return obj
    obj = model(**filters, **(defaults or {}))
    db.add(obj)
    db.flush()
    return obj


def main() -> None:
    db = SessionLocal()
    try:
        # ------------------------------------------------ tenant / org / user ---
        tenant = get_or_create(db, Tenant, name="FocalReach Demo Tenant")
        org = get_or_create(db, Organization, tenant_id=tenant.id, name="FocalReach Demo")
        user = db.scalars(select(User).where(User.email == DEMO_EMAIL)).first()
        if not user:
            user = User(
                organization_id=org.id,
                email=DEMO_EMAIL,
                full_name="FocalReach Admin",
                hashed_password=hash_password(DEMO_PASSWORD),
                role=UserRole.USER,
                is_active=True,
                must_change_password=False,
            )
            db.add(user)
            db.flush()

        # A "connected" mailbox with a fake app password: display works everywhere,
        # and any real SMTP/IMAP attempt fails — which is itself a failure case to watch.
        get_or_create(
            db, MailboxConnection, user_id=user.id, email_address=DEMO_EMAIL,
            defaults=dict(
                provider=MailboxProvider.GOOGLE,
                imap_host="imap.gmail.com", imap_port=993,
                smtp_host="smtp.gmail.com", smtp_port=587,
                encrypted_app_password=encrypt_secret("demo-not-a-real-app-password"),
                is_connected=True,
            ),
        )

        # -------------------------------------------- wipe previous demo run ---
        old = db.scalars(
            select(Campaign).where(Campaign.user_id == user.id, Campaign.name == CAMPAIGN_NAME)
        ).first()
        if old:
            old_li_id = old.lead_import_id
            db.delete(old)
            db.flush()
            if old_li_id:
                li = db.get(LeadImport, old_li_id)
                if li:
                    db.delete(li)  # cascades: companies, leads, drafts, replies, bookings, notifications
            db.flush()

        # -------------------------------------- campaign artifact chain ---------
        analysis = get_or_create(
            db, WebsiteAnalysis, organization_id=org.id, domain="focalreach.ai",
            defaults=dict(
                url="https://focalreach.ai", status=AnalysisStatus.COMPLETED,
                page_title="FocalReach — AI outbound engine",
                extracted_content="FocalReach automates outbound: lead scoring, outreach, and discovery-call booking.",
            ),
        )
        intel = get_or_create(
            db, CompanyIntelligence, website_analysis_id=analysis.id,
            defaults=dict(
                company_name="FocalReach", industry="B2B SaaS",
                summary="AI outbound engine: import leads, qualify, personalize outreach, book discovery calls.",
                business_model="B2B SaaS",
                services=[{"name": "Outbound automation", "description": "End-to-end outreach pipeline"}],
                value_propositions=["Book more discovery calls with less manual work"],
            ),
        )
        icp = ICP(
            company_intelligence_id=intel.id, user_id=user.id,
            campaign_objective="Book discovery calls with mid-market ops leaders evaluating outbound tooling.",
            target_industries=["SaaS", "Retail Tech", "Cloud Services"],
            company_size_ranges=[{"min": 50, "max": 500, "label": "51-500"}],
            target_roles=["Head of Sales", "VP Revenue", "RevOps Lead"],
            target_keywords=["outbound", "sales automation"],
            target_seniorities=["Director", "VP", "C-level"],
            target_geographies=["US", "EU", "APAC"],
        )
        db.add(icp)
        db.flush()

        li = LeadImport(
            icp_id=icp.id, user_id=user.id, organization_id=org.id,
            filename="demo_leads.csv", status=ImportStatus.SCORED, total_rows=12,
            raw_columns=["Name", "Email", "Company", "Country", "Title"],
            column_mapping={"full_name": "Name", "email": "Email", "company": "Company", "country": "Country"},
        )
        db.add(li)
        db.flush()

        campaign = Campaign(
            user_id=user.id, organization_id=org.id, name=CAMPAIGN_NAME,
            website_analysis_id=analysis.id, company_intelligence_id=intel.id,
            icp_id=icp.id, lead_import_id=li.id,
        )
        db.add(campaign)
        db.flush()

        # ----------------------------------------------------- companies --------
        def company(name, country, industry, status=QualificationStatus.APPROVED, **kw):
            c = Company(lead_import_id=li.id, name=name, country=country, industry=industry,
                        qualification_status=status, **kw)
            db.add(c)
            db.flush()
            return c

        acme = company("Acme Analytics", "United States", "SaaS", employee_count=220,
                       domain="acmeanalytics.com", website="https://acmeanalytics.com")
        brightline = company("Brightline GmbH", "Germany", "Cloud Services", employee_count=140,
                             domain="brightline.de")
        nimbus = company("Nimbus Retail", "UK", "Retail Tech", employee_count=310, domain="nimbusretail.co.uk")
        kaizen = company("Kaizen Cloud", "Japan", "Cloud Services", employee_count=95, domain="kaizencloud.jp")
        atlantis = company("Atlantis Ventures", "Atlantis", "Unknown",
                           status=QualificationStatus.REVIEW)  # unresolvable-country edge

        # --------------------------------------------------------- leads --------
        def lead(full_name, comp, email, country, title, tier=LeadTier.WARM, score=62.0, **kw):
            first = full_name.split()[0]
            l = Lead(
                lead_import_id=li.id, company_id=comp.id, full_name=full_name, first_name=first,
                email=email, country=country, title=title, tier=tier, total_score=score,
                role_score=24.0, signal_score=18.0, company_fit_score=20.0, **kw,
            )
            db.add(l)
            db.flush()
            return l

        priya = lead("Priya Sharma", acme, "priya.sharma@acmeanalytics.com", "India",
                     "VP Revenue", LeadTier.HOT, 78.0, timezone="Asia/Kolkata")
        daniel = lead("Daniel Weber", brightline, "d.weber@brightline.de", "Germany", "Head of Sales",
                      LeadTier.HOT, 74.0)
        emily = lead("Emily Carter", nimbus, "emily.carter@nimbusretail.co.uk", "UK", "RevOps Lead",
                     outreach_paused=True)
        raj = lead("Raj Patel", kaizen, "raj.patel@kaizencloud.jp", "United States", "Director of Ops",
                   outreach_paused=True)
        sofia = lead("Sofia Rossi", acme, "sofia.rossi@acmeanalytics.com", "Italy", "VP Marketing")
        liam = lead("Liam O'Brien", nimbus, "liam.obrien@nimbusretail.co.uk", "UK", "Head of Growth")
        chen = lead("Chen Wei", kaizen, "chen.wei@kaizencloud.jp", "China", "COO")
        ana = lead("Ana Souza", brightline, "ana.souza@brightline.de", "Brazil", "Sales Director")
        tom = lead("Tom Books", atlantis, None, "Atlantis", "Founder",  # no email + junk country
                   LeadTier.DEPRIORITIZED, 12.0)
        maya = lead("Maya Kim", acme, "maya.kim@acmeanalytics.com", "South Korea", "VP Sales",
                    is_duplicate=True, duplicate_reason="Active in campaign 'Q2 Outbound' (same email).")
        noah = lead("Noah Chapman", nimbus, "noah.chapman@nimbusretail.co.uk", "UK", "CRO")
        isabella = lead("Isabella Garcia", acme, "isabella.garcia@acmeanalytics.com", "Mexico", "VP Ops")
        lucas = lead("Lucas Martin", brightline, "lucas.martin@brightline.de", "France", "CEO")
        olivia = lead("Olivia Brown", nimbus, "olivia.brown@nimbusretail.co.uk", "UK", "Head of Partnerships")
        ethan = lead("Ethan Wright", acme, "ethan.wright@acmeanalytics.com", "Canada", "VP Product")

        # ---------------------------------------------------- email drafts ------
        def draft(l, status, step=STEP_INITIAL, subject=None, body=None, **kw):
            d = EmailDraft(
                lead_id=l.id, status=status, step_index=step,
                subject=subject or f"Quick question about {l.company.name}'s outbound",
                body=body or (
                    f"Hi {l.first_name},\n\nNoticed {l.company.name} is scaling — most teams your size lose "
                    "hours a week to manual prospecting. FocalReach automates the whole loop, from lead scoring "
                    "to booked discovery calls.\n\nWorth a 20-minute look?\n\nBest,\nFocalReach Admin"
                ),
                **kw,
            )
            db.add(d)
            db.flush()
            return d

        def dlog(d, outcome, detail=None, attempt=1):
            db.add(DispatchLog(draft_id=d.id, attempt=attempt, scheduled_for=d.scheduled_at,
                               outcome=outcome, detail=detail, message_id=d.message_id))

        # Sent conversations (these four got replies below)
        for i, l in enumerate((priya, daniel, emily, raj)):
            d = draft(l, DraftStatus.SENT, sent_at=NOW - timedelta(days=2, hours=i),
                      message_id=f"<demo-sent-{l.id}@focalreach.com>", attempt_count=1)
            dlog(d, "sent")

        # READY — sitting in the outreach workspace awaiting Send/Schedule
        draft(sofia, DraftStatus.READY)

        # SCHEDULED in the future — visible countdown, cancellable
        d_liam = draft(liam, DraftStatus.SCHEDULED, scheduled_at=NOW + timedelta(days=1, minutes=7),
                       scheduled_by_user_id=user.id, attempt_count=0)
        dlog(d_liam, "scheduled", "Booked for tomorrow (demo).")

        # SCHEDULED but OVERDUE — the dispatcher will claim it and fail on the fake
        # SMTP password: watch it land back in READY with the error message.
        d_isabella_overdue = draft(isabella, DraftStatus.SCHEDULED, scheduled_at=NOW - timedelta(minutes=9),
                                   scheduled_by_user_id=user.id, attempt_count=0)
        dlog(d_isabella_overdue, "scheduled", "Overdue on purpose — exercises the live dispatch-failure path.")

        # STUCK IN SENDING — interrupted mid-send 25 min ago. The sweeper will try
        # Sent-folder verification (fails on fake IMAP creds) -> NEEDS_ATTENTION + bell.
        d_chen = draft(chen, DraftStatus.SENDING, message_id=f"<demo-stuck-{chen.id}@focalreach.com>",
                       attempt_count=1, scheduled_at=NOW - timedelta(minutes=26),
                       scheduled_by_user_id=user.id)
        d_chen.updated_at = NOW - timedelta(minutes=25)
        dlog(d_chen, "scheduled", "Claimed and then 'crashed' mid-send (demo).")

        # NEEDS_ATTENTION — already flagged, resolve buttons available
        d_ana = draft(ana, DraftStatus.NEEDS_ATTENTION,
                      message_id=f"<demo-attention-{ana.id}@focalreach.com>", attempt_count=2,
                      error_message="Dispatch was interrupted mid-send and the outcome could not be verified "
                                    "automatically. Check your mailbox's Sent folder "
                                    f"(Message-ID <demo-attention-{ana.id}@focalreach.com>) before retrying.")
        dlog(d_ana, "stuck", "Flagged for manual resolution (demo).", attempt=2)

        # FAILED — AI generation failed for the lead with no email/junk country
        draft(tom, DraftStatus.FAILED, subject=None, body=None,
              error_message="Generation failed: the lead has no email address and the company has no website to personalize from.")

        # Auto-sent scheduling reply after Daniel's positive reply (step 100)
        draft(daniel, DraftStatus.SCHEDULED, step=STEP_SCHEDULING_REPLY,
              subject="Re: Quick question about Brightline GmbH's outbound",
              body="Great — could you share a date and time that works for you (and your timezone)? "
                   "I'll get something on the calendar right away.",
              scheduled_at=NOW + timedelta(minutes=3), scheduled_by_user_id=user.id)

        # Alternatives email for Isabella's unavailable slot (step 101)
        draft(isabella, DraftStatus.SCHEDULED, step=STEP_SLOT_ALTERNATIVES,
              subject="Re: booking a call",
              body="Thanks for proposing a time! Unfortunately that slot isn't available. Here are some "
                   "alternatives:\n- Monday, Jul 20 at 10:00 AM CST\n- Tuesday, Jul 21 at 2:30 PM CST\n"
                   "- Wednesday, Jul 22 at 9:00 AM CST\n\nReply with whichever works, or propose another time.",
              scheduled_at=NOW + timedelta(minutes=5), scheduled_by_user_id=user.id)

        # ------------------------------------------------- inbound replies ------
        mailbox = db.scalars(select(MailboxConnection).where(MailboxConnection.user_id == user.id)).first()
        _uid = [1000]

        def reply(l, intent, subject, body, confidence=0.95, reason="demo classification",
                  processing_error=None):
            _uid[0] += 1
            r = InboundReply(
                mailbox_connection_id=mailbox.id, lead_id=l.id, imap_uid=_uid[0],
                imap_message_id=f"<demo-reply-{l.id}-{_uid[0]}@{(l.email or 'x@y').split('@')[-1]}>",
                from_address=l.email, subject=subject, body_text=body,
                received_at=NOW - timedelta(hours=3),
                intent=intent, intent_confidence=confidence, intent_reason=reason,
                processed_at=NOW - timedelta(hours=2, minutes=55), processing_error=processing_error,
            )
            db.add(r)
            db.flush()
            return r

        r_priya = reply(priya, ReplyIntent.BOOKED, "Re: Quick question",
                        "This looks useful. Can we do Monday 2pm IST?")
        reply(daniel, ReplyIntent.POSITIVE, "Re: Quick question", "Sounds interesting — tell me more about pricing.")
        reply(emily, ReplyIntent.NEUTRAL, "Re: Quick question", "We're mid-reorg, circle back next quarter.")
        reply(raj, ReplyIntent.NEGATIVE, "Re: Quick question", "Not interested, please remove me from your list.")
        r_noah = reply(noah, ReplyIntent.BOOKED, "Re: Quick question",
                       "Yes let's talk — sometime late next week works.", confidence=0.9)
        r_isabella = reply(isabella, ReplyIntent.BOOKED, "Re: booking a call", "How about Saturday at 9pm?")
        r_lucas = reply(lucas, ReplyIntent.BOOKED, "Re: Quick question", "Tuesday 10am CET works, book it.")
        r_olivia = reply(olivia, ReplyIntent.BOOKED, "Re: Quick question", "Thursday 3pm GMT?")
        r_ethan = reply(ethan, ReplyIntent.BOOKED, "Re: Quick question", "Friday 11am ET is perfect.")
        # Unroutable reply — processing error recorded, kept for audit
        reply(maya, None, "Re: hello?", "Who is this? (demo: classification crashed)",
              confidence=0.0, reason="", processing_error="Demo: LLM was unreachable when this reply arrived.")

        # ------------------------------------------------ pending bookings ------
        def booking(l, r, status, resolved_start=None, resolved_tz=None, source=None,
                    last_error=None, uid=None, url=None, raw=None, stale_minutes=None):
            b = PendingBooking(
                lead_id=l.id, inbound_reply_id=r.id, user_id=user.id, status=status,
                resolved_start=resolved_start, resolved_timezone=resolved_tz,
                timezone_source=source, raw_extraction=raw or {"demo": True},
                last_error=last_error, calcom_booking_uid=uid, meeting_url=url,
            )
            if stale_minutes:
                b.updated_at = NOW - timedelta(minutes=stale_minutes)
                b.created_at = NOW - timedelta(minutes=stale_minutes)
            db.add(b)
            db.flush()
            return b

        next_monday = (NOW + timedelta(days=(7 - NOW.weekday()) % 7 or 7)).replace(
            hour=8, minute=30, second=0, microsecond=0)  # 14:00 IST as UTC

        # PENDING — the orchestrator (or its sweep) will pick this up; with Cal.com not
        # connected it demonstrates the automatic NEEDS_REVIEW downgrade.
        booking(priya, r_priya, PendingBookingStatus.PENDING, next_monday, "Asia/Kolkata",
                TimezoneSource.EXPLICIT,
                raw={"found": True, "date": str(next_monday.date()), "time": "14:00", "timezone": "Asia/Kolkata",
                     "confidence": 0.95})

        # NEEDS_REVIEW — ambiguous reply, no extractable time
        booking(noah, r_noah, PendingBookingStatus.NEEDS_REVIEW, source=TimezoneSource.UNKNOWN,
                raw={"found": False, "date": None, "time": None, "timezone": None, "confidence": 0.2})

        # NEEDS_REVIEW — automation failed with a recorded reason
        booking(olivia, r_olivia, PendingBookingStatus.NEEDS_REVIEW,
                NOW + timedelta(days=6, hours=5), "Europe/London", TimezoneSource.EXPLICIT,
                last_error="Cal.com is not connected.")

        # AWAITING_RESLOT — requested Saturday night; alternatives already emailed
        booking(isabella, r_isabella, PendingBookingStatus.AWAITING_RESLOT,
                NOW + timedelta(days=2), "America/Mexico_City", TimezoneSource.LEAD_COUNTRY)

        # BOOKED — success case with meeting link
        booking(lucas, r_lucas, PendingBookingStatus.BOOKED,
                NOW + timedelta(days=4, hours=2), "Europe/Paris", TimezoneSource.EXPLICIT,
                uid="demo-booking-uid-001", url="https://cal.com/focalreach/discovery/demo-booking-uid-001")

        # BOOKING — stuck mid-claim 20 min ago; booking.sweep_stale will flag it
        booking(ethan, r_ethan, PendingBookingStatus.BOOKING,
                NOW + timedelta(days=1, hours=6), "America/Toronto", TimezoneSource.EXPLICIT,
                stale_minutes=20)

        # ------------------------------------------------- notifications --------
        def notify(l, kind, detail, due_step_index=None):
            db.add(Notification(user_id=user.id, lead_id=l.id, kind=kind, detail=detail,
                                due_step_index=due_step_index))

        notify(priya, "reply_booked", "Wants to book: Mon 2:00 PM IST. Booking it on Cal.com automatically…")
        notify(daniel, "reply_positive", "Interested — asked for their availability: “Sounds interesting — tell me more about pricing.”")
        notify(emily, "reply_neutral", "Wants to wait: “We're mid-reorg, circle back next quarter.” — outreach paused. "
                                       "You can reply manually now, or resume outreach from the lead's page when the timing is right.")
        notify(raj, "reply_negative", "Not interested: “Not interested, please remove me from your list.”")
        notify(noah, "booking_needs_review", "Couldn't book automatically: the reply had no clear date/time — review it.")
        notify(isabella, "booking_alternatives", "Requested time (Saturday 9:00 PM) wasn't available — emailed 3 alternative slots.")
        notify(lucas, "booking_confirmed", "Meeting booked: Monday, Jul 20 at 10:00 AM CEST ✅")
        notify(ana, "dispatch_needs_attention", "An email to ana.souza@brightline.de was interrupted mid-send — open Outreach to resolve it.")
        notify(daniel, "follow_up_due", None, due_step_index=2)

        db.commit()

        print("Demo campaign seeded successfully.")
        print(f"  Login:    {DEMO_EMAIL} / {DEMO_PASSWORD}")
        print(f"  Campaign: {CAMPAIGN_NAME}  (public_id={campaign.public_id})")
        print(f"  Leads: 15 | Drafts: every status | Replies: every intent | Bookings: every status | Bell: 9 kinds")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
