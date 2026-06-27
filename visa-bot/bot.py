"""
VFS Global Visa Appointment Bot
Monitors VFS Global (Edinburgh) for available Italy Schengen visa appointments
and sends email/terminal notifications when slots open up.

Usage:
    pip install playwright playwright-stealth python-dotenv
    playwright install chromium   # only needed first time
    cp .env.example .env          # fill in your credentials
    python bot.py
"""

from __future__ import annotations

import os
import random
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

try:
    from playwright_stealth import stealth_sync
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False

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

# Poll between POLL_MIN and POLL_MAX seconds to avoid robotic fixed intervals
POLL_MIN = int(os.getenv("POLL_MIN_SECONDS", "240"))   # 4 min
POLL_MAX = int(os.getenv("POLL_MAX_SECONDS", "420"))   # 7 min

NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "")
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")
AUTO_BOOK = os.getenv("AUTO_BOOK", "false").lower() == "true"

# Run with a visible browser window (harder to detect, recommended)
HEADLESS = os.getenv("HEADLESS", "false").lower() == "true"

# Realistic user agents to rotate through
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
]

# Login page selectors — update if VFS changes their HTML
SEL_EMAIL = 'input[type="email"], input[name="email"], #mat-input-0'
SEL_PASSWORD = 'input[type="password"], input[name="password"], #mat-input-1'
SEL_SIGN_IN = 'button[type="submit"], button:has-text("Sign In"), button:has-text("Login")'
SEL_BOOK_APPT = 'a:has-text("Book Appointment"), button:has-text("Book Appointment")'
SEL_NO_SLOTS = ':text("No slots"), :text("no appointment"), :text("fully booked")'


# ---------------------------------------------------------------------------
# Human-like helpers
# ---------------------------------------------------------------------------

def _pause(lo: float = 0.8, hi: float = 2.5) -> None:
    """Sleep a random amount to mimic human reading/thinking time."""
    time.sleep(random.uniform(lo, hi))


def _human_type(page: Page, selector: str, text: str) -> None:
    """Type one character at a time with random delays like a real person."""
    page.click(selector)
    _pause(0.3, 0.7)
    for char in text:
        page.keyboard.type(char)
        time.sleep(random.uniform(0.05, 0.18))


def _human_click(page: Page, selector: str, timeout: int = 10_000) -> None:
    """Move mouse to element and click with a small random offset."""
    el = page.wait_for_selector(selector, timeout=timeout)
    if el:
        box = el.bounding_box()
        if box:
            x = box["x"] + box["width"] * random.uniform(0.3, 0.7)
            y = box["y"] + box["height"] * random.uniform(0.3, 0.7)
            page.mouse.move(x + random.uniform(-5, 5), y + random.uniform(-5, 5))
            _pause(0.1, 0.4)
            page.mouse.click(x, y)
        else:
            el.click()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def notify(slots: list[dict], page_url: str) -> None:
    lines = [f"  • {s['date']}  {s.get('time', '')}  ({s.get('location', '')})" for s in slots]
    body = "\n".join(lines)
    print("\n" + "=" * 60)
    print(f"[{_now()}]  *** SLOTS FOUND for {VFS_CITY} → {VFS_COUNTRY} ***")
    print(body)
    print(f"\nBook here: {page_url}")
    print("=" * 60 + "\n")

    if NOTIFY_EMAIL and GMAIL_USER and GMAIL_APP_PASSWORD:
        _send_email(slots, page_url, body)


def _send_email(slots: list[dict], page_url: str, body_text: str) -> None:
    subject = f"[VFS Bot] {len(slots)} appointment(s) found — {VFS_CITY} → {VFS_COUNTRY}"
    html = f"""
    <h2>VFS Global Appointment Slots Found!</h2>
    <p><strong>{VFS_CITY} → {VFS_COUNTRY} ({VFS_VISA_CATEGORY})</strong></p>
    <ul>
      {''.join(f"<li>{s['date']}  {s.get('time', '')}  {s.get('location', '')}</li>" for s in slots)}
    </ul>
    <p><a href="{page_url}" style="font-size:18px;font-weight:bold;color:green">
      Click here to book now — act fast!
    </a></p>
    <p><em>Sent by your VFS appointment bot.</em></p>
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

def login(page: Page) -> None:
    print(f"[{_now()}]  Navigating to VFS Global...")
    page.goto(VFS_URL, wait_until="networkidle", timeout=60_000)
    _pause(1.5, 3.0)

    # Accept cookies if banner appears
    for cookie_sel in [
        '#onetrust-accept-btn-handler',
        'button:has-text("Accept All")',
        'button:has-text("I accept")',
        'button:has-text("Accept")',
    ]:
        try:
            _human_click(page, cookie_sel, timeout=4_000)
            _pause(0.5, 1.2)
            break
        except (PlaywrightTimeout, Exception):
            continue

    # Click Sign In link if on landing page
    for sign_in_sel in [
        'a:has-text("Sign In")',
        'button:has-text("Sign In")',
        'a:has-text("Login")',
    ]:
        try:
            _human_click(page, sign_in_sel, timeout=4_000)
            page.wait_for_load_state("networkidle", timeout=15_000)
            _pause(1.0, 2.0)
            break
        except (PlaywrightTimeout, Exception):
            continue

    print(f"[{_now()}]  Logging in as {VFS_EMAIL}...")
    _human_type(page, SEL_EMAIL, VFS_EMAIL)
    _pause(0.5, 1.5)
    _human_type(page, SEL_PASSWORD, VFS_PASSWORD)
    _pause(0.8, 1.8)
    _human_click(page, SEL_SIGN_IN)
    page.wait_for_load_state("networkidle", timeout=20_000)
    _pause(1.5, 3.0)

    if "login" in page.url.lower() or "sign-in" in page.url.lower():
        raise RuntimeError(
            "Login may have failed — still on login page. "
            "Check VFS_EMAIL and VFS_PASSWORD in your .env file."
        )
    print(f"[{_now()}]  Logged in successfully.")


def navigate_to_booking(page: Page) -> None:
    _human_click(page, SEL_BOOK_APPT, timeout=15_000)
    page.wait_for_load_state("networkidle", timeout=20_000)
    _pause(1.0, 2.5)

    for sel in [
        f'mat-option:has-text("{VFS_COUNTRY}")',
        f'option:has-text("{VFS_COUNTRY}")',
    ]:
        try:
            _human_click(page, sel, timeout=4_000)
            _pause(0.5, 1.5)
            break
        except (PlaywrightTimeout, Exception):
            continue

    for sel in [
        f'mat-option:has-text("{VFS_CITY}")',
        f'option:has-text("{VFS_CITY}")',
    ]:
        try:
            _human_click(page, sel, timeout=4_000)
            _pause(0.5, 1.5)
            break
        except (PlaywrightTimeout, Exception):
            continue

    for sel in [
        f'mat-option:has-text("{VFS_VISA_CATEGORY}")',
        f'option:has-text("{VFS_VISA_CATEGORY}")',
    ]:
        try:
            _human_click(page, sel, timeout=4_000)
            _pause(0.5, 1.5)
            break
        except (PlaywrightTimeout, Exception):
            continue

    for btn in ["Continue", "Next", "Proceed"]:
        try:
            _human_click(page, f'button:has-text("{btn}")', timeout=4_000)
            page.wait_for_load_state("networkidle", timeout=15_000)
            _pause(1.0, 2.0)
            break
        except (PlaywrightTimeout, Exception):
            continue


def scrape_available_slots(page: Page) -> list[dict]:
    slots: list[dict] = []

    for _ in range(6):
        month_text = page.text_content(".mat-calendar-period-button, .calendar-header, h2.month") or ""
        month_match = re.search(r"(\w+ \d{4})", month_text)
        current_ym = ""
        if month_match:
            try:
                dt = datetime.strptime(month_match.group(1), "%B %Y")
                current_ym = dt.strftime("%Y-%m")
            except ValueError:
                pass

        if current_ym in TARGET_MONTHS:
            cells = page.query_selector_all(
                ".mat-calendar-body-cell:not(.mat-calendar-body-disabled), "
                ".day:not(.disabled):not(.unavailable), "
                "[class*='available']"
            )
            for cell in cells:
                label = (cell.get_attribute("aria-label") or cell.inner_text()).strip()
                if label:
                    slots.append({"date": label, "location": VFS_CITY})

        if current_ym and current_ym > max(TARGET_MONTHS):
            break

        for sel in [
            'button[aria-label="Next month"]',
            ".mat-calendar-next-button",
            'button:has-text(">")',
            ".next-month",
        ]:
            try:
                _human_click(page, sel, timeout=3_000)
                _pause(0.5, 1.2)
                break
            except (PlaywrightTimeout, Exception):
                continue
        else:
            break

    return slots


def check_no_slots_message(page: Page) -> bool:
    try:
        page.wait_for_selector(SEL_NO_SLOTS, timeout=3_000)
        return True
    except PlaywrightTimeout:
        return False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_once(page: Page) -> list[dict]:
    try:
        login(page)
        navigate_to_booking(page)

        if check_no_slots_message(page):
            print(f"[{_now()}]  No slots available (page says so explicitly).")
            return []

        slots = scrape_available_slots(page)
        if slots:
            notify(slots, page.url)
        else:
            print(f"[{_now()}]  No slots found for {', '.join(TARGET_MONTHS)} in {VFS_CITY}.")
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
    print("  VFS Global Appointment Bot  (stealth edition)")
    print(f"  Target : {VFS_CITY} → {VFS_COUNTRY} ({VFS_VISA_CATEGORY})")
    print(f"  Months : {', '.join(TARGET_MONTHS)}")
    print(f"  Polling: every {POLL_MIN}–{POLL_MAX}s (randomised)")
    print(f"  Notify : {NOTIFY_EMAIL or 'terminal only'}")
    print(f"  Stealth: {'ON' if STEALTH_AVAILABLE else 'OFF (pip install playwright-stealth)'}")
    print(f"  Browser: {'headless' if HEADLESS else 'visible window'}")
    print("=" * 60 + "\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )

        while True:
            user_agent = random.choice(USER_AGENTS)
            context = browser.new_context(
                user_agent=user_agent,
                viewport={"width": random.randint(1200, 1440), "height": random.randint(750, 900)},
                locale="en-GB",
                timezone_id="Europe/London",
            )
            # Mask automation signals via JS
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
                window.chrome = { runtime: {} };
            """)

            page = context.new_page()
            if STEALTH_AVAILABLE:
                stealth_sync(page)

            try:
                slots = run_once(page)
                if slots and AUTO_BOOK:
                    print(f"[{_now()}]  AUTO_BOOK=true — attempting to book first slot...")
                    _auto_book(page, slots[0])
            finally:
                page.close()
                context.close()

            wait = random.randint(POLL_MIN, POLL_MAX)
            print(f"[{_now()}]  Sleeping {wait}s until next check...\n")
            time.sleep(wait)


def _auto_book(page: Page, slot: dict) -> None:
    print(f"[{_now()}]  Auto-book: selecting {slot['date']}...")
    try:
        _human_click(page, f"[aria-label*='{slot['date']}']:not(.mat-calendar-body-disabled)")
        _pause(1.0, 2.0)
        for btn in ["Confirm", "Book", "Submit"]:
            try:
                _human_click(page, f'button:has-text("{btn}")', timeout=4_000)
                page.wait_for_load_state("networkidle", timeout=15_000)
                print(f"[{_now()}]  Auto-book: clicked '{btn}'. Check email for confirmation.")
                break
            except (PlaywrightTimeout, Exception):
                continue
    except Exception as exc:
        print(f"[{_now()}]  Auto-book failed: {exc}")


if __name__ == "__main__":
    missing = [v for v in ("VFS_EMAIL", "VFS_PASSWORD") if not os.environ.get(v)]
    if missing:
        print(f"ERROR: Missing required env vars: {', '.join(missing)}")
        print("Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)
    main()
