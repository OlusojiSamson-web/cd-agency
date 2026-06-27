"""
VFS Global Visa Appointment Bot
Monitors VFS Global (Edinburgh) for available Italy Schengen visa appointments
and sends email/terminal notifications when slots open up.

Usage:
    pip install playwright python-dotenv
    playwright install chromium   # only needed first time
    cp .env.example .env          # fill in your credentials
    python bot.py
"""

from __future__ import annotations

import os
import re
import smtplib
import sys
import time
import traceback
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

load_dotenv(Path(__file__).parent / ".env")

VFS_URL = "https://visa.vfsglobal.com/gbr/en/ita"
VFS_EMAIL = os.environ["VFS_EMAIL"]
VFS_PASSWORD = os.environ["VFS_PASSWORD"]
VFS_COUNTRY = os.getenv("VFS_COUNTRY", "Italy")
VFS_CITY = os.getenv("VFS_CITY", "Edinburgh")
VFS_VISA_CATEGORY = os.getenv("VFS_VISA_CATEGORY", "Schengen Visa")

TARGET_MONTHS_RAW = os.getenv("TARGET_MONTHS", "2025-07,2025-08")
TARGET_MONTHS: list[str] = [m.strip() for m in TARGET_MONTHS_RAW.split(",")]

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
AUTO_BOOK = os.getenv("AUTO_BOOK", "false").lower() == "true"

# Login page selectors — update if VFS changes their HTML
SEL_EMAIL = 'input[type="email"], input[name="email"], #mat-input-0'
SEL_PASSWORD = 'input[type="password"], input[name="password"], #mat-input-1'
SEL_SIGN_IN = 'button[type="submit"], button:has-text("Sign In"), button:has-text("Login")'
SEL_BOOK_APPT = 'a:has-text("Book Appointment"), button:has-text("Book Appointment")'
SEL_NO_SLOTS = ':text("No slots"), :text("no appointment"), :text("fully booked")'


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def notify(slots: list[dict], page_url: str) -> None:
    """Print to terminal and email when slots are found."""
    lines = [f"  • {s['date']}  {s.get('time', '')}  ({s.get('location', '')})" for s in slots]
    body = "\n".join(lines)
    print("\n" + "=" * 60)
    print(f"[{_now()}]  SLOTS FOUND for {VFS_CITY} → {VFS_COUNTRY}")
    print(body)
    print(f"\nBook here: {page_url}")
    print("=" * 60 + "\n")

    if NOTIFY_EMAIL and GMAIL_USER and GMAIL_APP_PASSWORD:
        _send_email(slots, page_url, body)


def _send_email(slots: list[dict], page_url: str, body_text: str) -> None:
    subject = f"[VFS Bot] {len(slots)} appointment(s) found — {VFS_CITY} → {VFS_COUNTRY}"
    html = f"""
    <h2>VFS Global Appointment Slots Found</h2>
    <p><strong>{VFS_CITY} → {VFS_COUNTRY} ({VFS_VISA_CATEGORY})</strong></p>
    <ul>
      {''.join(f"<li>{s['date']}  {s.get('time', '')}  {s.get('location', '')}</li>" for s in slots)}
    </ul>
    <p><a href="{page_url}">Click here to book now</a></p>
    <p><em>This alert was sent by your VFS appointment bot. Act fast — slots go quickly!</em></p>
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = NOTIFY_EMAIL
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as srv:
            srv.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            srv.sendmail(GMAIL_USER, NOTIFY_EMAIL, msg.as_string())
        print(f"[{_now()}]  Email sent to {NOTIFY_EMAIL}")
    except Exception as exc:
        print(f"[{_now()}]  Email failed: {exc}")


# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def login(page: Page) -> None:
    print(f"[{_now()}]  Navigating to VFS Global...")
    page.goto(VFS_URL, wait_until="networkidle", timeout=60_000)

    # Accept cookies if banner appears
    try:
        page.click('button:has-text("Accept"), button:has-text("I accept"), #onetrust-accept-btn-handler', timeout=5_000)
    except PlaywrightTimeout:
        pass

    # Click Sign In link if on landing page
    try:
        page.click('a:has-text("Sign In"), button:has-text("Sign In")', timeout=5_000)
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightTimeout:
        pass

    print(f"[{_now()}]  Logging in as {VFS_EMAIL}...")
    page.fill(SEL_EMAIL, VFS_EMAIL)
    page.fill(SEL_PASSWORD, VFS_PASSWORD)
    page.click(SEL_SIGN_IN)
    page.wait_for_load_state("networkidle", timeout=20_000)

    if "login" in page.url.lower() or "sign-in" in page.url.lower():
        raise RuntimeError(
            "Login may have failed — still on login page. "
            "Check VFS_EMAIL and VFS_PASSWORD in your .env file."
        )
    print(f"[{_now()}]  Logged in successfully.")


def navigate_to_booking(page: Page) -> None:
    """Click through to the appointment calendar for Italy / Edinburgh."""
    page.click(SEL_BOOK_APPT, timeout=15_000)
    page.wait_for_load_state("networkidle", timeout=20_000)

    # Select country (Italy)
    for sel in [
        f'mat-option:has-text("{VFS_COUNTRY}")',
        f'option:has-text("{VFS_COUNTRY}")',
        f'[aria-label*="country"] >> text={VFS_COUNTRY}',
    ]:
        try:
            page.click(sel, timeout=4_000)
            break
        except PlaywrightTimeout:
            continue

    # Select city / VAC (Edinburgh)
    for sel in [
        f'mat-option:has-text("{VFS_CITY}")',
        f'option:has-text("{VFS_CITY}")',
    ]:
        try:
            page.click(sel, timeout=4_000)
            break
        except PlaywrightTimeout:
            continue

    # Select visa category
    for sel in [
        f'mat-option:has-text("{VFS_VISA_CATEGORY}")',
        f'option:has-text("{VFS_VISA_CATEGORY}")',
    ]:
        try:
            page.click(sel, timeout=4_000)
            break
        except PlaywrightTimeout:
            continue

    # Click Continue / Next
    for btn in ["Continue", "Next", "Proceed"]:
        try:
            page.click(f'button:has-text("{btn}")', timeout=4_000)
            page.wait_for_load_state("networkidle", timeout=15_000)
            break
        except PlaywrightTimeout:
            continue


def scrape_available_slots(page: Page) -> list[dict]:
    """
    Parse the appointment calendar for available dates in TARGET_MONTHS.
    VFS renders a month-by-month calendar; enabled dates have CSS classes
    like 'available', 'open', or lack the 'disabled' class.
    """
    slots: list[dict] = []

    # Navigate months until we reach target months
    for _ in range(6):  # look up to 6 months ahead
        month_text = page.text_content(".mat-calendar-period-button, .calendar-header, h2.month") or ""
        month_match = re.search(r"(\w+ \d{4})", month_text)
        if month_match:
            current_label = month_match.group(1)
            # Convert "July 2025" → "2025-07"
            try:
                dt = datetime.strptime(current_label, "%B %Y")
                current_ym = dt.strftime("%Y-%m")
            except ValueError:
                current_ym = ""
        else:
            current_ym = ""

        if current_ym in TARGET_MONTHS:
            # Find available (non-disabled) date cells
            cells = page.query_selector_all(
                ".mat-calendar-body-cell:not(.mat-calendar-body-disabled), "
                ".day:not(.disabled):not(.unavailable), "
                "[class*='available']"
            )
            for cell in cells:
                label = (cell.get_attribute("aria-label") or cell.inner_text()).strip()
                if label:
                    slots.append({"date": label, "location": VFS_CITY})

        # Check if we've passed all target months
        if current_ym and current_ym > max(TARGET_MONTHS):
            break

        # Click "Next month" arrow
        for sel in [
            'button[aria-label="Next month"]',
            ".mat-calendar-next-button",
            'button:has-text(">")',
            ".next-month",
        ]:
            try:
                page.click(sel, timeout=3_000)
                page.wait_for_timeout(800)
                break
            except PlaywrightTimeout:
                continue
        else:
            break  # couldn't advance, stop

    return slots


def check_no_slots_message(page: Page) -> bool:
    """Return True if the page explicitly says no slots are available."""
    try:
        page.wait_for_selector(SEL_NO_SLOTS, timeout=3_000)
        return True
    except PlaywrightTimeout:
        return False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_once(page: Page) -> list[dict]:
    """One check cycle. Returns list of available slots (empty if none)."""
    try:
        login(page)
        navigate_to_booking(page)

        if check_no_slots_message(page):
            print(f"[{_now()}]  No slots available (explicit message on page).")
            return []

        slots = scrape_available_slots(page)
        if slots:
            notify(slots, page.url)
        else:
            months_str = ", ".join(TARGET_MONTHS)
            print(f"[{_now()}]  No slots found for {months_str} in {VFS_CITY}.")
        return slots

    except PlaywrightTimeout as exc:
        print(f"[{_now()}]  Timeout — page may have changed layout: {exc}")
        return []
    except Exception as exc:
        print(f"[{_now()}]  Error during check: {exc}")
        traceback.print_exc()
        return []


def main() -> None:
    print("=" * 60)
    print("  VFS Global Appointment Bot")
    print(f"  Target: {VFS_CITY} → {VFS_COUNTRY} ({VFS_VISA_CATEGORY})")
    print(f"  Months: {', '.join(TARGET_MONTHS)}")
    print(f"  Polling every {POLL_INTERVAL}s  |  Notify: {NOTIFY_EMAIL or 'terminal only'}")
    print(f"  Auto-book: {AUTO_BOOK}")
    print("=" * 60 + "\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            executable_path="/opt/pw-browsers/chromium",
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        while True:
            page = context.new_page()
            try:
                slots = run_once(page)
                if slots and AUTO_BOOK:
                    print(f"[{_now()}]  AUTO_BOOK=true — attempting to book first slot...")
                    _auto_book(page, slots[0])
            finally:
                page.close()

            print(f"[{_now()}]  Sleeping {POLL_INTERVAL}s until next check...\n")
            time.sleep(POLL_INTERVAL)


def _auto_book(page: Page, slot: dict) -> None:
    """
    EXPERIMENTAL: click the first available date to confirm a booking.
    Only runs when AUTO_BOOK=true. You must verify the booking yourself.
    """
    print(f"[{_now()}]  Auto-book: selecting {slot['date']}...")
    try:
        page.click(f"[aria-label*='{slot['date']}']:not(.mat-calendar-body-disabled)", timeout=5_000)
        page.wait_for_timeout(1000)
        # Click Confirm/Book button
        for btn in ["Confirm", "Book", "Submit"]:
            try:
                page.click(f'button:has-text("{btn}")', timeout=4_000)
                page.wait_for_load_state("networkidle", timeout=15_000)
                print(f"[{_now()}]  Auto-book: clicked '{btn}'. Check your email for confirmation.")
                break
            except PlaywrightTimeout:
                continue
    except Exception as exc:
        print(f"[{_now()}]  Auto-book failed: {exc}")


if __name__ == "__main__":
    # Validate required config
    missing = [v for v in ("VFS_EMAIL", "VFS_PASSWORD") if not os.environ.get(v)]
    if missing:
        print(f"ERROR: Missing required env vars: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)
    main()
