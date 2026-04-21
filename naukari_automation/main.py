from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
import re
import os
import logging
import logging.handlers
import json
from datetime import datetime
import csv
import sys
from ai_agent import AIAgent
from config import PERSONAL_INFO, NAUKRI_EMAIL, NAUKRI_PASSWORD, RESUME_PATH

# ============ LOGGING (rotating, max 2 MB × 3 backups) ============
_log_handler = logging.handlers.RotatingFileHandler(
    'naukri_agent.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8'
)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    handlers=[_log_handler, logging.StreamHandler()]
)

APPLIED_JOBS_FILE  = 'applied_jobs.json'
OUTCOMES_LOG_FILE  = 'outcomes.csv'


def _init_outcomes_log():
    """Create outcomes CSV with header if it doesn't exist."""
    if not os.path.exists(OUTCOMES_LOG_FILE):
        with open(OUTCOMES_LOG_FILE, 'w', newline='', encoding='utf-8') as f:
            csv.writer(f).writerow([
                'timestamp', 'job_title', 'company', 'url',
                'result', 'questions_answered'
            ])


class NaukriAgent:

    def __init__(self, profile):
        self.profile = profile
        self.driver = None
        self.wait   = None
        self.main_window = None

        self.applied          = 0
        self.already_applied  = 0
        self.failed           = 0
        self.external_apply   = 0
        self.questions_answered = 0

        self.external_apply_jobs    = []
        self.current_job_title      = None
        self.current_job_naukri_link = None
        self.in_iframe              = False

        self.ai_agent = AIAgent()

        # Persist applied jobs across restarts to avoid re-applying
        self.applied_job_urls = self._load_applied_jobs()

        # Initialize outcomes log CSV
        _init_outcomes_log()

    def _log_outcome(self, job_title, company, url, result, q_answered=0):
        """Append one row to the outcomes CSV."""
        try:
            with open(OUTCOMES_LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                csv.writer(f).writerow([
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    job_title, company, url, result, q_answered
                ])
        except Exception as e:
            logging.warning(f"Could not write outcome log: {e}")

    # ================================================================
    # Applied-job persistence helpers
    # ================================================================

    def _load_applied_jobs(self):
        """Load previously applied job IDs from disk."""
        try:
            if os.path.exists(APPLIED_JOBS_FILE):
                with open(APPLIED_JOBS_FILE, 'r') as f:
                    data = json.load(f)
                ids = set(data.get('applied_urls', []))
                logging.info(f"📂 Loaded {len(ids)} previously applied jobs from cache")
                return ids
        except Exception as e:
            logging.warning(f"Could not load applied jobs cache: {e}")
        return set()

    def _save_applied_jobs(self):
        """Persist applied job IDs to disk."""
        try:
            with open(APPLIED_JOBS_FILE, 'w') as f:
                json.dump({
                    'applied_urls':   list(self.applied_job_urls),
                    'last_updated':   datetime.now().isoformat(),
                    'total_applied':  self.applied,
                }, f, indent=2)
        except Exception as e:
            logging.error(f"Could not save applied jobs: {e}")

    def _extract_job_id(self, url):
        """Extract the numeric Naukri job ID from a URL (e.g. '-23456789')."""
        try:
            match = re.search(r'-(\d{7,10})(?:\?|$|/)', url)
            if match:
                return match.group(1)
            return url  # fallback: use full URL as key
        except Exception:
            return url

    # ================================================================
    # Driver alive check
    # ================================================================

    def check_driver_alive(self):
        """Check if the Chrome driver is still connected."""
        try:
            _ = self.driver.current_window_handle
            return True
        except Exception:
            return False

    # ================================================================
    # iFrame helpers (chatbot questionnaires)
    # ================================================================

    def switch_to_chatbot_iframe(self):
        """Check if chatbot is inside an iframe and switch to it."""
        try:
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            logging.info(f"  Found {len(iframes)} iframe(s) on page")
            for idx, iframe in enumerate(iframes):
                try:
                    is_visible = self.driver.execute_script(
                        "return arguments[0].offsetWidth > 0 && arguments[0].offsetHeight > 0;",
                        iframe
                    )
                    if not is_visible:
                        continue

                    iframe_src   = iframe.get_attribute('src')   or 'no-src'
                    iframe_id    = iframe.get_attribute('id')    or 'no-id'
                    iframe_class = iframe.get_attribute('class') or 'no-class'
                    logging.info(
                        f"  Trying iframe {idx}: id={iframe_id}, "
                        f"class={iframe_class}, src={iframe_src[:80]}"
                    )

                    self.driver.switch_to.frame(iframe)

                    body      = self.driver.find_element(By.TAG_NAME, "body")
                    body_text = body.text.strip()
                    body_html = body.get_attribute('innerHTML')

                    has_inputs = len(self.driver.find_elements(By.XPATH,
                        "//input | //textarea | //div[@contenteditable='true'] | //div[@role='textbox']"
                    )) > 0
                    has_chatbot = any(
                        kw in (body_html or '').lower()
                        for kw in ['chatbot', 'chat', 'question', 'message', 'send', 'submit', 'radio', 'option']
                    )

                    if has_inputs or (has_chatbot and len(body_text) > 10):
                        logging.info(
                            f"  ✓ Switched to iframe {idx} - "
                            f"has_inputs={has_inputs}, has_chatbot={has_chatbot}, text_len={len(body_text)}"
                        )
                        logging.info(f"  Iframe body text (first 300): {body_text[:300]}")
                        self.in_iframe = True
                        return True
                    else:
                        logging.info(f"  Iframe {idx} has no relevant content, switching back")
                        self.driver.switch_to.default_content()
                except Exception as e:
                    logging.warning(f"  Error with iframe {idx}: {str(e)[:100]}")
                    try:
                        self.driver.switch_to.default_content()
                    except Exception:
                        pass
                    continue
        except Exception as e:
            logging.warning(f"  Error finding iframes: {str(e)[:100]}")
        return False

    def switch_back_from_iframe(self):
        """Switch back to main content if we're in an iframe."""
        if self.in_iframe:
            try:
                self.driver.switch_to.default_content()
                self.in_iframe = False
                logging.info("  Switched back from iframe to main content")
            except Exception:
                pass

    # ================================================================
    # Browser / Chrome setup
    # ================================================================

    def setup_driver(self):
        options = webdriver.ChromeOptions()
        options.add_argument('--start-maximized')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])

        # Reuse a dedicated Chrome profile so the login session persists between runs.
        # Delete the folder 'naukri_chrome_profile' to force a fresh login.
        chrome_profile = os.path.abspath('naukri_chrome_profile')
        options.add_argument(f'--user-data-dir={chrome_profile}')
        options.add_argument('--profile-directory=NaukriBot')

        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        self.wait        = WebDriverWait(self.driver, 15)
        self.main_window = self.driver.current_window_handle
        logging.info("Driver initialized")

    # ================================================================
    # Login
    # ================================================================

    def login(self):
        """Automated login — skips if already logged in via saved Chrome profile."""
        try:
            self.driver.get("https://www.naukri.com/nlogin/login")

            # Smart wait: wait up to 5s for page to settle, then check URL
            try:
                WebDriverWait(self.driver, 5).until(
                    lambda d: 'naukri.com' in d.current_url
                )
            except TimeoutException:
                pass

            # If the profile remembered the session, we land on home/dashboard
            if "nlogin" not in self.driver.current_url and "login" not in self.driver.current_url:
                logging.info("✅ Already logged in via saved Chrome profile — skipping login step")
                self.main_window = self.driver.current_window_handle
                return

            logging.info(f"Attempting automated login for: {NAUKRI_EMAIL}")

            email_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, "usernameField"))
            )
            email_field.clear()
            email_field.send_keys(NAUKRI_EMAIL)

            pass_field = self.driver.find_element(By.ID, "passwordField")
            pass_field.clear()
            pass_field.send_keys(NAUKRI_PASSWORD)

            login_btn = None
            for selector in [
                "//button[@type='submit']",
                "//button[contains(text(), 'Login')]",
                "//button[contains(@class, 'loginButton')]",
                "//div[contains(@class, 'login')]//button",
                "//*[@id='loginForm']//button",
            ]:
                try:
                    btn = self.driver.find_element(By.XPATH, selector)
                    if btn.is_displayed():
                        login_btn = btn
                        break
                except Exception:
                    continue

            if login_btn:
                try:
                    login_btn.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", login_btn)
            else:
                raise Exception("Could not find Login button with any selector")

            # Smart wait: wait up to 8s for URL to leave login page
            try:
                WebDriverWait(self.driver, 8).until(
                    lambda d: 'login' not in d.current_url
                )
            except TimeoutException:
                pass

            if "login" in self.driver.current_url:
                print("\n" + "!" * 70)
                print("Automated login might be stuck (Captcha / Wrong credentials).")
                print("Please complete the login manually in the browser window.")
                print("!" * 70)
                input("Press ENTER after you have successfully logged in...")

            self.main_window = self.driver.current_window_handle
            logging.info("Login process completed")

        except Exception as e:
            logging.error(f"Automated login failed: {str(e)}")
            print("\nAutomated login failed. Please login manually in the browser.")
            input("Press ENTER after logging in...")
            self.main_window = self.driver.current_window_handle

    # ================================================================
    # Search URL builder
    # ================================================================

    def build_search_urls(self):
        """
        Build a list of Naukri search URLs — one per keyword.
        All URLs include:
          jobAge=3          → only jobs posted in the last 3 days (newest first)
          sort=1            → sorted newest to oldest
          experience=lo,hi  → experience band around the profile's years
          salary=4,15       → 4–15 LPA salary band (avoids mismatched junior/senior roles)
        """
        urls     = []
        exp      = self.profile['experience_years']
        # Build a sensible experience range: 1 year below to 2 years above
        exp_min  = max(0, exp - 1)
        exp_max  = exp + 2
        keywords = self.profile.get('search_keywords',
                                    [self.profile.get('search_keyword', 'Java')])

        # First keyword: prefer custom_url if set
        custom_url = self.profile.get('custom_url', '').strip()
        if custom_url:
            urls.append(custom_url)
            remaining_keywords = keywords[1:]
        else:
            remaining_keywords = keywords

        for kw in remaining_keywords:
            kw_slug    = kw.replace(' ', '-').lower()
            kw_encoded = kw.replace(' ', '+')
            urls.append(
                f"https://www.naukri.com/{kw_slug}-jobs"
                f"?k={kw_encoded}"
                f"&experience={max(0, exp - 1)},{exp + 2}"
                f"&jobAge=3"
                f"&sort=1"
            )

        return urls

    # ================================================================
    # Job link collector (pre-scrape to avoid stale elements)
    # ================================================================

    def get_all_job_links(self):
        """
        Harvest every job title link on the current search results page
        UPFRONT — before navigating anywhere — so we never hit stale
        element errors when looping.

        Returns a list of dicts: {href, title, company}
        """
        jobs = []
        try:
            try:
                # Wait for job links to appear
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//a[contains(@class, 'title')]"))
                )
            except Exception:
                pass

            elements = []
            for sel in [
                "//a[contains(@class, 'title') and contains(@href, 'job-listings')]",
                "//a[contains(@class, 'title ')]",
                "//a[contains(@class, 'title fw500')]",
                "//a[contains(@class, 'title')]",
            ]:
                elements = self.driver.find_elements(By.XPATH, sel)
                if any('naukri.com' in (e.get_attribute('href') or '') for e in elements):
                    break

            for elem in elements:
                try:
                    href  = elem.get_attribute('href') or ''
                    title = elem.text.strip()
                    if not href or not title or 'naukri.com' not in href:
                        continue

                    # Try to grab company name directly from the job card (no page nav needed)
                    company = "Unknown"
                    try:
                        card = elem.find_element(By.XPATH,
                            "./ancestor::div[contains(@class, 'srp-job-tuple')] | "
                            "./ancestor::article"
                        )
                        comp_elem = card.find_element(By.XPATH,
                            ".//a[contains(@class, 'comp-name')] | "
                            ".//div[contains(@class, 'comp-name')]"
                        )
                        company = comp_elem.text.strip()
                    except Exception:
                        pass

                    jobs.append({'href': href, 'title': title, 'company': company})
                except Exception:
                    continue

        except Exception as e:
            logging.error(f"Error collecting job links: {e}")

        return jobs

    # ================================================================
    # Sidebar / questionnaire helpers (unchanged from original)
    # ================================================================

    def find_sidebar_container(self):
        """Find the sidebar/modal container."""
        sidebar_selectors = [
            "//div[contains(@class, 'drawer') and contains(@class, 'open')]",
            "//div[contains(@class, 'sidebar') and contains(@style, 'display')]",
            "//div[contains(@class, 'modal') and contains(@class, 'show')]",
            "//div[contains(@class, 'slideInRight')]",
            "//div[@role='dialog']",
            "//div[contains(@class, 'questionDrawer')]",
            "//aside[contains(@class, 'drawer')]",
            "//div[contains(@class, 'chatbot')]",
            "//div[contains(@class, 'chat-window')]",
            "//div[contains(@class, 'layer')]",
        ]

        for selector in sidebar_selectors:
            try:
                sidebars = self.driver.find_elements(By.XPATH, selector)
                for sidebar in sidebars:
                    try:
                        is_visible = self.driver.execute_script(
                            "return arguments[0].offsetWidth > 0 && arguments[0].offsetHeight > 0;",
                            sidebar
                        )
                        if is_visible:
                            logging.info(f"  Found sidebar with selector: {selector}")
                            return sidebar
                    except Exception:
                        continue
            except Exception:
                continue

        return None

    def _get_job_context(self):
        """Return a short job context string for AI prompts."""
        parts = []
        if self.current_job_title:
            parts.append(f"Job: {self.current_job_title}")
        try:
            company_name, _ = self.extract_company_info()
            if company_name and company_name != "Unknown":
                parts.append(f"Company: {company_name}")
        except Exception:
            pass
        return " | ".join(parts) if parts else None

    def _extract_question_from_sidebar(self, sidebar, inp):
        """
        Try multiple strategies to find the question text for a given input element.
        Returns the best question string found, or empty string.
        """
        question = ""

        # Strategy 1: label[@for] matching input id
        try:
            field_id = inp.get_attribute('id')
            if field_id:
                label = sidebar.find_element(By.XPATH, f".//label[@for='{field_id}']")
                if label.text.strip():
                    return label.text.strip()
        except Exception:
            pass

        # Strategy 2: aria-label attribute
        question = inp.get_attribute('aria-label') or ""
        if question.strip():
            return question.strip()

        # Strategy 3: placeholder
        question = inp.get_attribute('placeholder') or ""
        if question.strip():
            return question.strip()

        # Strategy 4: nearest preceding sibling / parent text
        try:
            parent = inp.find_element(By.XPATH, "./ancestor::div[contains(@class,'question') or "
                                                "contains(@class,'field') or contains(@class,'form')][1]")
            texts = [t.strip() for t in parent.text.split('\n')
                     if t.strip() and len(t.strip()) > 3
                     and not any(s in t.lower() for s in ['submit', 'save', 'next', 'skip'])]
            if texts:
                return texts[0]
        except Exception:
            pass

        # Strategy 5: all visible text in sidebar, reversed (most recent question last)
        try:
            all_text_elems = sidebar.find_elements(By.XPATH,
                ".//*[not(self::script) and not(self::style) and "
                "not(self::input) and not(self::textarea)]"
            )
            sidebar_texts = []
            for te in all_text_elems:
                try:
                    t = te.text.strip()
                    if t and 3 < len(t) < 200:
                        sidebar_texts.append(t)
                except Exception:
                    pass
            skip_texts = ['save', 'submit', 'next', 'skip', 'type message', 'type here', 'send']
            for text in reversed(sidebar_texts):
                if not any(s in text.lower() for s in skip_texts):
                    return text
        except Exception:
            pass

        return "field"

    def fill_current_question(self, sidebar):
        """
        Fill ALL fields visible in the CURRENT question only.
        Now passes field_type and options to AI for smarter answers.
        Returns True if any field was filled.
        """
        filled_any  = False
        job_context = self._get_job_context()

        # Diagnostic dump (debug only — comment out in production to speed up)
        try:
            sidebar_html = sidebar.get_attribute('innerHTML')
            logging.info(f"    SIDEBAR HTML (first 1500 chars): {sidebar_html[:1500]}")
        except Exception as e:
            logging.warning(f"    Could not dump sidebar HTML: {str(e)[:100]}")

        # ---- TEXT INPUTS ----
        try:
            inputs = sidebar.find_elements(By.XPATH,
                ".//input[@type='text'] | .//input[@type='number'] | "
                ".//textarea | .//input[not(@type)]"
            )
            chatbot_inputs = sidebar.find_elements(By.XPATH,
                ".//div[@contenteditable='true'] | "
                ".//div[@role='textbox'] | "
                ".//span[@contenteditable='true'] | "
                ".//p[@contenteditable='true'] | "
                ".//div[contains(@class, 'input') and not(contains(@class, 'input-wrapper'))] | "
                ".//div[contains(@class, 'reply')] | "
                ".//div[contains(@class, 'message-input')] | "
                ".//div[contains(@class, 'chat-input')]"
            )

            if self.in_iframe and len(inputs) == 0:
                inputs = self.driver.find_elements(By.XPATH,
                    "//input[@type='text'] | //input[@type='number'] | "
                    "//textarea | //input[not(@type)]"
                )
                chatbot_inputs_global = self.driver.find_elements(By.XPATH,
                    "//div[@contenteditable='true'] | "
                    "//div[@role='textbox'] | "
                    "//span[@contenteditable='true'] | "
                    "//p[@contenteditable='true']"
                )
                chatbot_inputs = chatbot_inputs + chatbot_inputs_global

            all_inputs = inputs + chatbot_inputs
            logging.info(
                f"    Found {len(inputs)} standard + {len(chatbot_inputs)} chatbot inputs "
                f"= {len(all_inputs)} total"
            )

            for inp in all_inputs:
                try:
                    if not self.check_driver_alive():
                        raise WebDriverException("Driver disconnected")

                    is_visible = self.driver.execute_script(
                        "return arguments[0].offsetWidth > 0 && arguments[0].offsetHeight > 0;",
                        inp
                    )
                    if not is_visible:
                        try:
                            parent = inp.find_element(By.XPATH, "./..")
                            is_visible = self.driver.execute_script(
                                "return arguments[0].offsetWidth > 0 && arguments[0].offsetHeight > 0;",
                                parent
                            )
                        except Exception:
                            pass
                    if not is_visible:
                        continue

                    current_value = inp.get_attribute('value') or ''
                    if current_value and len(current_value) > 0:
                        continue

                    # Determine field type from HTML attribute
                    raw_type   = inp.get_attribute('type') or 'text'
                    field_type = 'number' if raw_type == 'number' else 'text'
                    tag        = inp.tag_name.lower()
                    if tag == 'textarea':
                        field_type = 'textarea'

                    # Extract question intelligently
                    question = self._extract_question_from_sidebar(sidebar, inp)

                    if any(x in question.lower() for x in ['search', 'filter', 'keyword']):
                        continue

                    answer = self.ai_agent.answer_question(
                        question, field_type=field_type, job_context=job_context
                    )

                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});", inp
                    )
                    time.sleep(0.3)

                    logging.info(f"    Filling ({field_type}) '{question[:30]}' → '{answer}'")

                    fill_success = False
                    try:
                        inp.click()
                        inp.clear()
                        inp.send_keys(answer)
                        fill_success = True
                        logging.info(f"    ✓ Filled (Standard): {answer}")
                    except Exception as e:
                        logging.warning(f"      Standard fill failed: {str(e)[:60]}")

                    if not fill_success:
                        try:
                            safe_answer = answer.replace("'", "\\'")
                            self.driver.execute_script(f"arguments[0].value = '{safe_answer}';", inp)
                            self.driver.execute_script("""
                                arguments[0].dispatchEvent(new Event('input',  { bubbles: true }));
                                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                                arguments[0].dispatchEvent(new Event('blur',   { bubbles: true }));
                            """, inp)
                            fill_success = True
                            logging.info(f"    ✓ Filled (JS): {answer}")
                        except Exception as e:
                            logging.warning(f"      JS fill failed: {str(e)[:60]}")

                    if fill_success:
                        filled_any = True
                        self.questions_answered += 1
                        time.sleep(0.3)

                except Exception as e:
                    logging.error(f"    Error processing input: {str(e)[:80]}")
                    continue

        except Exception as e:
            logging.error(f"    Error finding inputs: {str(e)}")

        # ---- RADIO / OPTION TEXT STRATEGY ----
        try:
            positive_keywords = ["Yes", "yes", "Agree", "agree", "Willing", "willing", "Okay", "okay"]
            candidate_elements = sidebar.find_elements(By.XPATH,
                ".//*[not(self::script) and not(self::style)]"
            )
            clicked_option = False

            for elem in candidate_elements:
                try:
                    text = elem.text.strip()
                    if not text or len(text) > 20:
                        continue
                    if any(text.lower() == k.lower() for k in positive_keywords):
                        is_visible = self.driver.execute_script(
                            "return arguments[0].offsetWidth > 0 && arguments[0].offsetHeight > 0;",
                            elem
                        )
                        if is_visible:
                            logging.info(f"    Found potential answer option: '{text}'")
                            self.driver.execute_script(
                                "arguments[0].style.border='2px solid red'", elem
                            )
                            self.driver.execute_script(
                                "arguments[0].scrollIntoView({block: 'center'});", elem
                            )
                            time.sleep(0.3)
                            try:
                                elem.click()
                                logging.info(f"    ✓ Clicked option: {text}")
                                clicked_option = True
                                filled_any = True
                                break
                            except Exception:
                                try:
                                    self.driver.execute_script("arguments[0].click();", elem)
                                    logging.info(f"    ✓ JS Clicked option: {text}")
                                    clicked_option = True
                                    filled_any = True
                                    break
                                except Exception:
                                    try:
                                        parent = elem.find_element(By.XPATH, "./..")
                                        self.driver.execute_script("arguments[0].click();", parent)
                                        logging.info(f"    ✓ JS Clicked parent option: {text}")
                                        clicked_option = True
                                        filled_any = True
                                        break
                                    except Exception:
                                        pass
                except Exception:
                    continue

            if clicked_option:
                time.sleep(0.5)

        except Exception as e:
            logging.error(f"    Error processing text options: {str(e)}")

        # ---- TRADITIONAL RADIO BUTTONS — AI-driven ----
        if not filled_any:
            try:
                radios = sidebar.find_elements(By.XPATH, ".//input[@type='radio']")
                if radios:
                    # Group radios by name to find distinct questions
                    radio_groups = {}
                    for radio in radios:
                        name = radio.get_attribute('name') or 'default'
                        radio_groups.setdefault(name, []).append(radio)

                    for name, group in radio_groups.items():
                        try:
                            # Collect option labels
                            option_labels = []
                            radio_label_map = {}
                            for radio in group:
                                radio_id = radio.get_attribute('id')
                                label_text = ""
                                if radio_id:
                                    try:
                                        lbl = sidebar.find_element(By.XPATH, f".//label[@for='{radio_id}']")
                                        label_text = lbl.text.strip()
                                    except Exception:
                                        pass
                                if not label_text:
                                    try:
                                        label_text = radio.find_element(By.XPATH, "./..").text.strip()
                                    except Exception:
                                        pass
                                if label_text:
                                    option_labels.append(label_text)
                                    radio_label_map[label_text] = radio

                            if not option_labels:
                                # Fallback: click first "yes/agree" radio
                                for radio in group:
                                    try:
                                        parent_text = radio.find_element(By.XPATH, "./..").text.lower()
                                        if any(x in parent_text for x in ['yes', 'agree', 'willing']):
                                            self.driver.execute_script("arguments[0].click();", radio)
                                            filled_any = True
                                            break
                                    except Exception:
                                        pass
                                continue

                            # Get question context for this radio group
                            question = self._extract_question_from_sidebar(sidebar, group[0])
                            best = self.ai_agent.answer_question(
                                question, field_type='radio',
                                options=option_labels, job_context=job_context
                            )

                            # Find and click the matching radio
                            matched_radio = self.ai_agent._match_to_options(best, option_labels)
                            if matched_radio in radio_label_map:
                                self.driver.execute_script(
                                    "arguments[0].click();", radio_label_map[matched_radio]
                                )
                                logging.info(f"    ✓ Radio selected: '{matched_radio}' for '{question[:30]}'")
                                filled_any = True
                            else:
                                # Default: first option in group
                                self.driver.execute_script("arguments[0].click();", group[0])
                                logging.info(f"    ✓ Radio fallback: '{option_labels[0]}'")
                                filled_any = True
                        except Exception:
                            pass
            except Exception:
                pass

        # ---- DROPDOWNS ----
        try:
            dropdowns = sidebar.find_elements(By.XPATH, ".//select")
            for dropdown in dropdowns:
                try:
                    is_visible = self.driver.execute_script(
                        "return arguments[0].offsetWidth > 0 && arguments[0].offsetHeight > 0;",
                        dropdown
                    )
                    if not is_visible:
                        continue

                    self.driver.execute_script(
                        "arguments[0].scrollIntoView({block: 'center'});", dropdown
                    )
                    time.sleep(0.2)

                    select  = Select(dropdown)
                    options = select.options
                    if len(options) <= 1:
                        continue

                    option_texts = [o.text.strip() for o in options if o.text.strip()]

                    # Get label for this dropdown
                    dropdown_label = self._extract_question_from_sidebar(sidebar, dropdown)

                    # Ask AI / smart picker
                    best_option = self.ai_agent.pick_dropdown_option(
                        dropdown_label, option_texts, job_context=job_context
                    )

                    if best_option:
                        try:
                            select.select_by_visible_text(best_option)
                            logging.info(f"    ✓ Dropdown selected: '{best_option}' for '{dropdown_label[:30]}'")
                            filled_any = True
                        except Exception:
                            # Fallback: pick first non-placeholder option
                            for opt in options:
                                if not any(p in opt.text.lower() for p in ['select', 'choose', '---', 'please']):
                                    try:
                                        select.select_by_visible_text(opt.text)
                                        logging.info(f"    ✓ Dropdown fallback: '{opt.text}'")
                                        filled_any = True
                                        break
                                    except Exception:
                                        pass
                    time.sleep(0.3)
                except Exception:
                    continue
        except Exception:
            pass

        # ---- CHECKBOXES ----
        try:
            checkboxes = sidebar.find_elements(By.XPATH, ".//input[@type='checkbox']")
            for cb in checkboxes:
                try:
                    is_visible = self.driver.execute_script(
                        "return arguments[0].offsetWidth > 0 && arguments[0].offsetHeight > 0;",
                        cb
                    )
                    if is_visible and not cb.is_selected():
                        self.driver.execute_script("arguments[0].click();", cb)
                        filled_any = True
                        logging.info("    ✓ Checked checkbox")
                        time.sleep(0.2)
                except Exception:
                    continue
        except Exception:
            pass

        return filled_any

    # ================================================================
    # Resume upload helper
    # ================================================================

    def try_upload_resume(self, container):
        """
        Detect and fill any file-upload input found in 'container'.
        Only uploads if the container text suggests it's asking for a resume or upload,
        to avoid blindly uploading files during standard chatbot questions.
        """
        # Guard: Only upload once per job
        if getattr(self, '_resume_uploaded_for_current_job', False):
            return False
            
        # Guard: Only upload if the container text actually mentions resumes or uploading
        container_text = container.text.lower()
        if not any(word in container_text for word in ['resume', 'cv', 'upload', 'attach']):
            return False

        resume_abs = os.path.abspath(RESUME_PATH)
        if not os.path.exists(resume_abs):
            logging.warning(f"    ⚠️  Resume not found at {resume_abs} — skipping upload")
            return False

        uploaded = False

        # When inside an iframe, also grab any file inputs at document level
        extra = []
        if self.in_iframe:
            try:
                extra = self.driver.find_elements(By.XPATH, "//input[@type='file']")
            except Exception:
                extra = []

        try:
            file_inputs = container.find_elements(By.XPATH, ".//input[@type='file']")
            file_inputs += extra
            for fi in file_inputs:
                try:
                    # Make hidden file inputs interactable via JS
                    self.driver.execute_script(
                        "arguments[0].style.display = 'block'; "
                        "arguments[0].style.visibility = 'visible';", fi
                    )
                    fi.send_keys(resume_abs)
                    logging.info(f"    📎 Resume uploaded: {resume_abs}")
                    uploaded = True
                    self._resume_uploaded_for_current_job = True
                    time.sleep(0.5)
                except Exception as e:
                    logging.warning(f"    Could not upload to file input: {str(e)[:80]}")
        except Exception as e:
            logging.warning(f"    Error searching for file inputs: {str(e)[:80]}")

        return uploaded

    def click_save_button(self, sidebar):
        """
        Find and click the Save / Send / Submit button.
        Searches inside sidebar first, then inside iframe (if active),
        then globally as fallback.
        """
        save_selectors = [
            ".//button[contains(@class, 'send')]",
            ".//button[contains(@class, 'Send')]",
            ".//button[@aria-label='Send']",
            ".//button[@aria-label='send']",
            ".//button[contains(@class, 'submit')]",
            ".//button[contains(@class, 'chat') and contains(@class, 'btn')]",
            ".//button[contains(@class, 'reply')]",
            ".//i[contains(@class, 'send')]/..",
            ".//svg[contains(@class, 'send')]/..",
            ".//button[normalize-space()='Send']",
            ".//button[normalize-space()='send']",
            ".//button[contains(text(), 'Send')]",
            ".//button[normalize-space()='Submit']",
            ".//button[contains(text(), 'Submit')]",
            ".//button[normalize-space()='Save']",
            ".//button[normalize-space()='save']",
            ".//button[normalize-space()='Next']",
            ".//button[contains(text(), 'Save')]",
            ".//div[contains(@class, 'save')]",
            ".//button[contains(@class, 'save')]",
            ".//button[@type='submit']",
            ".//button[contains(@class, 'action')]",
            ".//div[contains(@class, 'send')]",
            ".//a[contains(@class, 'send')]",
        ]

        logging.info("    Searching for Save button...")

        def _try_click(btn):
            try:
                is_visible = self.driver.execute_script(
                    "return arguments[0].offsetWidth > 0 && arguments[0].offsetHeight > 0;", btn
                )
            except Exception:
                is_visible = False
            if not is_visible:
                return False
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
            time.sleep(0.3)
            try:
                btn.click()
                return True
            except Exception:
                try:
                    self.driver.execute_script("arguments[0].click();", btn)
                    return True
                except Exception:
                    return False

        # 1. Inside sidebar
        for sel in save_selectors:
            try:
                for btn in sidebar.find_elements(By.XPATH, sel):
                    if _try_click(btn):
                        logging.info(f"    ✓ Clicked Save (sidebar, {sel})")
                        return True
            except Exception:
                continue

        # 2. Inside iframe
        if self.in_iframe:
            logging.info("    Searching within iframe globally for Save/Send button...")
            for sel in [s.replace(".//", "//") for s in save_selectors]:
                try:
                    for btn in self.driver.find_elements(By.XPATH, sel):
                        if _try_click(btn):
                            logging.info(f"    ✓ Clicked Save (iframe, {sel})")
                            return True
                except Exception:
                    continue

        # 3. Global fallback
        logging.info("    Save button not found in sidebar, searching globally...")
        was_in_iframe = self.in_iframe
        if was_in_iframe:
            self.switch_back_from_iframe()

        skip_texts = ['search', 'filter', 'login', 'register']
        for sel in [s.replace(".//", "//") for s in save_selectors]:
            try:
                for btn in self.driver.find_elements(By.XPATH, sel):
                    btn_text = btn.text.strip().lower()
                    if any(s in btn_text for s in skip_texts):
                        continue
                    if _try_click(btn):
                        logging.info(f"    ✓ Clicked Save (global, {sel})")
                        return True
            except Exception:
                continue

        logging.warning("    ❌ Save/Send button NOT found anywhere")
        return False

    def handle_sidebar_questionnaire(self):
        """
        One-question-at-a-time loop:
        1. Switch to iframe if present
        2. Fill current question
        3. Click Save/Send
        4. Wait for next question
        5. Repeat until sidebar closes
        """
        try:
            logging.info("  Checking for sidebar questionnaire...")

            question_number = 0
            max_questions   = 20
            iframe_checked  = False

            while question_number < max_questions:
                if not self.check_driver_alive():
                    logging.error("Driver disconnected during questionnaire")
                    self.switch_back_from_iframe()
                    return False

                time.sleep(2)  # Reduced from 4 s

                if self.in_iframe:
                    self.switch_back_from_iframe()

                sidebar = self.find_sidebar_container()
                if not sidebar:
                    logging.info(f"  Sidebar closed — completed {question_number} questions")
                    break

                question_number += 1
                logging.info(f"\n  === Question {question_number} ===")

                if not iframe_checked:
                    iframe_checked = True
                    iframe_found   = self.switch_to_chatbot_iframe()
                    if iframe_found:
                        logging.info("  Using iframe mode for questionnaire")
                        iframe_sidebar = self.find_sidebar_container()
                        if iframe_sidebar:
                            sidebar = iframe_sidebar
                    else:
                        logging.info("  No chatbot iframe found, using direct DOM mode")

                if not self.in_iframe and iframe_checked:
                    self.switch_to_chatbot_iframe()

                # Try uploading resume if a file input is present
                self.try_upload_resume(sidebar)

                filled_any = self.fill_current_question(sidebar)

                if not filled_any:
                    logging.info("    No fields to fill in this question")
                    if not self.click_save_button(sidebar):
                        logging.info("    No Save/Send button — questionnaire complete")
                        break
                    # Smart wait: wait up to 3s for sidebar to update
                    try:
                        WebDriverWait(self.driver, 3).until(
                            lambda d: self.find_sidebar_container() is not None
                        )
                    except TimeoutException:
                        time.sleep(1)
                    continue

                time.sleep(0.5)
                if self.click_save_button(sidebar):
                    logging.info("    Waiting for next question...")
                    # Smart wait: up to 4s for sidebar to refresh (new question loads)
                    try:
                        WebDriverWait(self.driver, 4).until(
                            lambda d: self.find_sidebar_container() is not None
                        )
                    except TimeoutException:
                        time.sleep(1)
                else:
                    logging.info("    No Save/Send button — might be last question")
                    time.sleep(1)
                    if self.in_iframe:
                        self.switch_back_from_iframe()
                    if not self.find_sidebar_container():
                        logging.info("    Sidebar closed — questionnaire complete")
                        break

            self.switch_back_from_iframe()
            logging.info(
                f"\n  ✅ Questionnaire complete — "
                f"answered {question_number} questions, filled {self.questions_answered} fields"
            )
            return True

        except Exception as e:
            self.switch_back_from_iframe()
            if "refused" in str(e) or "reset" in str(e):
                logging.critical("🔥 Connection lost with browser! Stopping questionnaire.")
                sys.exit(1)
            logging.error(f"Questionnaire error: {str(e)}")
            import traceback
            traceback.print_exc()
            return False

    # ================================================================
    # Company / external apply helpers
    # ================================================================

    def extract_company_info(self):
        try:
            company_name = "Unknown"
            job_title    = self.current_job_title or "Unknown"

            for selector in [
                "//div[contains(@class, 'comp-name')]",
                "//a[contains(@class, 'comp-name')]",
            ]:
                try:
                    elem = self.driver.find_element(By.XPATH, selector)
                    company_name = elem.text.strip()
                    if company_name:
                        break
                except Exception:
                    continue

            return company_name, job_title
        except Exception:
            return "Unknown", self.current_job_title or "Unknown"

    def detect_external_apply(self):
        try:
            current_url  = self.driver.current_url
            company_name, job_title = self.extract_company_info()

            if 'naukri.com' not in current_url:
                job_details = {
                    'naukri_job_link':    self.current_job_naukri_link or 'Not captured',
                    'external_apply_link': current_url,
                    'company_name':        company_name,
                    'job_title':           job_title,
                    'domain':              current_url.split('/')[2] if len(current_url.split('/')) > 2 else 'Unknown',
                    'timestamp':           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'apply_type':          'External Redirect',
                }
                return True, current_url, job_details

            page_text = self.driver.page_source.lower()
            for keyword in ['apply on company site', 'apply on company website', 'external apply']:
                if keyword in page_text:
                    external_link = current_url
                    try:
                        link_elem = self.driver.find_element(By.XPATH,
                            "//a[contains(@href, 'http') and not(contains(@href, 'naukri.com'))]"
                        )
                        external_link = link_elem.get_attribute('href')
                    except Exception:
                        pass

                    job_details = {
                        'naukri_job_link':    self.current_job_naukri_link or current_url,
                        'external_apply_link': external_link,
                        'company_name':        company_name,
                        'job_title':           job_title,
                        'domain':              external_link.split('/')[2] if 'http' in external_link else 'Company Website',
                        'timestamp':           datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'apply_type':          'Apply on Company Site',
                    }
                    return True, external_link, job_details

            return False, None, None
        except Exception:
            return False, None, None

    def save_external_apply_link(self, job_details):
        self.external_apply_jobs.append(job_details)
        logging.info(f"  EXTERNAL: {job_details['company_name']} - {job_details['domain']}")
        print(f"  [EXTERNAL] {job_details['company_name']} → saved for manual review")

    # ================================================================
    # Core: click Apply button on a job page and handle outcome
    # ================================================================

    def click_apply_and_handle(self):
        try:
            # Smart wait: wait up to 3s for page body to be present before checking
            try:
                WebDriverWait(self.driver, 3).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except TimeoutException:
                pass

            is_external, external_url, job_details = self.detect_external_apply()
            if is_external:
                logging.info(f"  ⏭️  External apply detected: {job_details['domain']} — skipping")
                self.external_apply += 1
                return 'skip'

            # Smart wait: wait up to 5s for Apply button to appear
            apply_btn = None
            apply_selectors = [
                "//button[contains(text(), 'Apply')]",
                "//button[contains(@class, 'apply')]",
                "//a[contains(text(), 'Apply')]",
            ]
            try:
                apply_btn = WebDriverWait(self.driver, 5).until(
                    lambda d: next(
                        (d.find_element(By.XPATH, sel)
                         for sel in apply_selectors
                         if self._elem_visible(d, sel)),
                        None
                    )
                )
            except TimeoutException:
                pass

            # Fallback linear search if smart wait timed out
            if not apply_btn:
                for selector in apply_selectors:
                    try:
                        btn = self.driver.find_element(By.XPATH, selector)
                        if btn.is_displayed():
                            apply_btn = btn
                            break
                    except Exception:
                        continue

            if not apply_btn:
                page_text = self.driver.page_source.lower()
                if 'applied' in page_text:
                    logging.info("  Already applied")
                    self.already_applied += 1
                    return 'already_applied'
                else:
                    self.failed += 1
                    return 'failed'

            try:
                apply_btn.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", apply_btn)

            logging.info("  Clicked Apply")

            # Smart wait: wait up to 4s for sidebar/redirect to appear after Apply click
            try:
                WebDriverWait(self.driver, 4).until(
                    lambda d: (
                        self.find_sidebar_container() is not None
                        or 'naukri.com' not in d.current_url
                        or len(d.window_handles) > 1
                        or len(d.find_elements(By.XPATH,
                            "//div[contains(@class,'drawer')] | //div[@role='dialog']")) > 0
                    )
                )
            except TimeoutException:
                time.sleep(1)

            if len(self.driver.window_handles) > 1:
                logging.info("  ⏭️  Apply opened a new tab/window (external site) — skipping")
                self.external_apply += 1
                return 'skip'

            is_external_after, _, ext_details = self.detect_external_apply()
            if is_external_after:
                logging.info("  ⏭️  External redirect after Apply — skipping")
                self.external_apply += 1
                return 'skip'

            # Reset the resume upload tracker for this specific job session
            self._resume_uploaded_for_current_job = False
            self.handle_sidebar_questionnaire()

            self.applied += 1
            return 'success'

        except Exception as e:
            logging.error(f"  Apply failed: {str(e)}")
            self.failed += 1
            return 'failed'

    def _elem_visible(self, driver, xpath):
        """Helper: return element only if it exists and is displayed, else None."""
        try:
            el = driver.find_element(By.XPATH, xpath)
            return el if el.is_displayed() else None
        except Exception:
            return None

    # ================================================================
    # Close any extra browser tabs (safety net)
    # ================================================================

    def close_extra_tabs(self):
        try:
            all_windows = self.driver.window_handles
            for window in all_windows:
                if window != self.main_window:
                    self.driver.switch_to.window(window)
                    self.driver.close()
            self.driver.switch_to.window(self.main_window)
            time.sleep(0.5)
        except Exception:
            pass

    # ================================================================
    # Process one job by URL (replaces index-based approach)
    # ================================================================

    def process_job_url(self, job_info):
        """
        Navigate directly to a job's URL and apply.
        Company exclusion check happens BEFORE any page load — zero wasted time.

        Returns (result_str, company_name)
        """
        href    = job_info['href']
        title   = job_info['title']
        company = job_info['company']

        # --- 1. Skip excluded companies BEFORE navigating (instant) ---
        excluded = self.profile.get('excluded_companies', [])
        if any(excl.lower() in company.lower() for excl in excluded):
            logging.info(f"  ⏭️  Skipping excluded company: {company}")
            return 'skip_company', company

        # --- 2. Skip if already applied (persisted across runs) ---
        job_id = self._extract_job_id(href)
        if job_id and job_id in self.applied_job_urls:
            logging.info(f"  ⏭️  Already applied (cached): {title[:40]}")
            self.already_applied += 1
            self._log_outcome(title, company, href, 'already_applied')
            return 'already_applied', company

        logging.info(f"\nProcessing: {title[:55]} @ {company}")
        self.current_job_title       = title
        self.current_job_naukri_link = href

        try:
            # Navigate directly to the job page
            self.driver.get(href)

            # Smart wait: wait for page to start loading (body present)
            try:
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
            except TimeoutException:
                time.sleep(1)

            result = self.click_apply_and_handle()

            if result == 'success':
                # Persist the job ID so we never apply twice
                if job_id:
                    self.applied_job_urls.add(job_id)
                    self._save_applied_jobs()
                self._log_outcome(title, company, href, 'applied', self.questions_answered)
            elif result in ('skip', 'external'):
                self._log_outcome(title, company, href, 'external')
            elif result == 'failed':
                self._log_outcome(title, company, href, 'failed')

            # Close any extra tabs that may have opened
            self.close_extra_tabs()
            return result, company

        except Exception as e:
            logging.error(f"Job error: {str(e)}")
            self.close_extra_tabs()
            self.failed += 1
            self._log_outcome(title, company, href, 'error')
            return 'failed', company

    # ================================================================
    # Pagination
    # ================================================================

    def go_to_next_page(self, current_page):
        try:
            logging.info(f"Navigating to page {current_page + 1}...")
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight - 500);")
            time.sleep(1.5)

            next_btn = None
            for selector in [
                "//a[contains(@class, 'next')]",
                "//a[contains(., 'Next')]",
                "//span[contains(text(), 'Next')]",
                "//button[contains(., 'Next')]",
                "//a[contains(@class, 'styles_btn') and contains(., 'Next')]",
            ]:
                try:
                    btn = self.driver.find_element(By.XPATH, selector)
                    if btn.is_displayed():
                        next_btn = btn
                        break
                except Exception:
                    continue

            if not next_btn:
                try:
                    target = str(current_page + 1)
                    next_btn = self.driver.find_element(By.XPATH,
                        f"//a[text()='{target}' or contains(@aria-label, 'Page {target}')]"
                    )
                except Exception:
                    pass

            if next_btn:
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", next_btn)
                time.sleep(0.3)
                try:
                    next_btn.click()
                except Exception:
                    self.driver.execute_script("arguments[0].click();", next_btn)
                time.sleep(2)
                return True

            logging.warning(f"Could not find 'Next' button or page {current_page + 1}")
            return False
        except Exception as e:
            logging.error(f"Pagination error: {str(e)}")
            return False

    # ================================================================
    # Save external links to files
    # ================================================================

    def save_external_links_to_files(self):
        try:
            if not self.external_apply_jobs:
                return

            timestamp    = datetime.now().strftime("%Y%m%d_%H%M%S")
            csv_filename = f"external_apply_links_{timestamp}.csv"
            json_filename = f"external_apply_links_{timestamp}.json"
            txt_filename  = f"external_apply_links_{timestamp}.txt"

            fieldnames = ['timestamp', 'job_title', 'company_name', 'domain',
                          'naukri_job_link', 'external_apply_link', 'apply_type']
            with open(csv_filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(self.external_apply_jobs)

            with open(json_filename, 'w', encoding='utf-8') as f:
                json.dump(self.external_apply_jobs, f, indent=2, ensure_ascii=False)

            with open(txt_filename, 'w', encoding='utf-8') as f:
                f.write("=" * 80 + "\n")
                f.write("EXTERNAL APPLY LINKS — FOR MANUAL APPLICATION\n")
                f.write("=" * 80 + "\n\n")
                for i, job in enumerate(self.external_apply_jobs, 1):
                    f.write(f"Job {i}:\n")
                    f.write(f"  Title:       {job['job_title']}\n")
                    f.write(f"  Company:     {job['company_name']}\n")
                    f.write(f"  Domain:      {job['domain']}\n")
                    f.write(f"  Apply Type:  {job['apply_type']}\n")
                    f.write(f"\n  NAUKRI LINK:\n  {job['naukri_job_link']}\n")
                    f.write(f"\n  EXTERNAL LINK:\n  {job['external_apply_link']}\n")
                    f.write(f"\n  Time: {job['timestamp']}\n")
                    f.write("\n" + "-" * 80 + "\n\n")

            print(f"\n{'='*70}")
            print(f"📁 EXTERNAL LINKS SAVED (apply manually)!")
            print(f"{'='*70}")
            print(f"CSV:   {csv_filename}")
            print(f"JSON:  {json_filename}")
            print(f"TXT:   {txt_filename}")
            print(f"Total: {len(self.external_apply_jobs)}")
            print(f"{'='*70}\n")

        except Exception as e:
            logging.error(f"Save error: {str(e)}")

    # ================================================================
    # Main run loop
    # ================================================================

    def run(self):
        try:
            self.setup_driver()
            self.login()

            max_apps         = self.profile['max_applications']
            jobs_per_keyword = self.profile.get('jobs_per_keyword', max_apps)
            search_urls      = self.build_search_urls()

            print(f"\n{'='*70}")
            print(f"🤖 NAUKRI AUTONOMOUS AI AGENT")
            print(f"{'='*70}")
            print(f"🔍 Keywords:           {len(search_urls)}")
            print(f"🎯 Target apps:        {max_apps}")
            print(f"📄 Jobs per keyword:   {jobs_per_keyword}")
            print(f"📂 Cached (skippable): {len(self.applied_job_urls)} jobs")
            print(f"⏱️  Recent only:        last 3 days | sorted newest first")
            print(f"{'='*70}\n")

            for url_idx, search_url in enumerate(search_urls):
                if self.applied >= max_apps:
                    break

                kw_applied = 0
                logging.info(f"\n{'='*70}")
                logging.info(f"KEYWORD {url_idx + 1}/{len(search_urls)}: {search_url}")
                logging.info(f"{'='*70}")

                # Navigate to this keyword's search results
                self.driver.get(search_url)
                time.sleep(4)
                current_page_url = self.driver.current_url

                page           = 1
                company_counts = {}
                max_repeat     = self.profile.get('max_company_repetition', 5)

                while self.applied < max_apps and kw_applied < jobs_per_keyword:
                    logging.info(f"\n--- Page {page} (keyword {url_idx + 1}) ---")

                    # PRE-SCRAPE all job links on this page (avoids stale element errors)
                    job_links = self.get_all_job_links()

                    if not job_links:
                        logging.info("No jobs found on this page — moving to next keyword")
                        break

                    logging.info(f"Found {len(job_links)} jobs on page {page}")

                    for job_info in job_links:
                        if self.applied >= max_apps or kw_applied >= jobs_per_keyword:
                            break

                        company = job_info['company']

                        # Skip if this company has appeared too many times on this search
                        if company != "Unknown":
                            company_counts[company] = company_counts.get(company, 0) + 1
                            if company_counts[company] > max_repeat:
                                logging.info(f"⚠️ '{company}' repeated >{max_repeat}×, skipping")
                                continue

                        result_data = self.process_job_url(job_info)
                        if not result_data:
                            continue

                        result     = result_data[0] if isinstance(result_data, tuple) else result_data
                        kw_applied += 1

                        # Short break every 10 successful applications
                        if self.applied > 0 and self.applied % 10 == 0:
                            logging.info("⏸️  Taking 5-second cooldown...")
                            time.sleep(5)

                    if self.applied >= max_apps or kw_applied >= jobs_per_keyword:
                        break

                    # Return to the current page of search results, then go to next page
                    self.driver.get(current_page_url)
                    time.sleep(1.5)

                    if not self.go_to_next_page(page):
                        logging.info("No more pages for this keyword")
                        break

                    current_page_url = self.driver.current_url
                    page += 1

                logging.info(
                    f"\n✅ Keyword {url_idx + 1} done — "
                    f"{kw_applied} jobs processed, {self.applied} total applied"
                )

            # Save external links for manual application
            if self.external_apply_jobs:
                self.save_external_links_to_files()

            print(f"\n{'='*70}")
            print(f"🎉 MISSION COMPLETE")
            print(f"{'='*70}")
            print(f"✅ Direct Applications:  {self.applied}")
            print(f"⏭️  Already Applied:      {self.already_applied}")
            print(f"⏭️  External (skipped):   {self.external_apply}")
            print(f"❌ Failed:               {self.failed}")
            print(f"📝 Questions Answered:   {self.questions_answered}")
            print(f"{'='*70}\n")

        except KeyboardInterrupt:
            print("\n\n⚠️  Interrupted by user — saving progress...")
            if self.external_apply_jobs:
                self.save_external_links_to_files()

        except Exception as e:
            logging.error(f"Fatal error in run(): {str(e)}")
            import traceback
            traceback.print_exc()

        finally:
            # Always persist applied jobs before closing
            self._save_applied_jobs()
            if self.driver:
                input("\nPress ENTER to close browser...")
                self.driver.quit()


# ================================================================
# Entry point
# ================================================================

if __name__ == "__main__":
    from config import PERSONAL_INFO

    print("\n" + "=" * 70)
    print("🤖 NAUKRI AUTONOMOUS AI AGENT")
    print("=" * 70)
    print("Mode: Recent jobs (last 3 days) • Sorted: Newest first")
    print("=" * 70 + "\n")

    agent = NaukriAgent(PERSONAL_INFO)
    agent.run()