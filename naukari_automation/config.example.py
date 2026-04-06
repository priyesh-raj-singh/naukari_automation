import os
from dotenv import load_dotenv

load_dotenv()

# ============ API KEYS ============
# Paste your Gemini API key below:
GEMINI_API_KEY = "YOUR_GEMINI_API_KEY_HERE"

# ============ LOGIN CREDENTIALS ============
# Fill in your actual Naukri credentials here
NAUKRI_EMAIL    = "your.email@example.com"
NAUKRI_PASSWORD = "your_actual_password_here"

# ============ RESUME ============
RESUME_PATH = os.getenv("RESUME_PATH", "Priyesh-Raj-Singh-cv.pdf")

# ============ PERSONAL INFO ============
PERSONAL_INFO = {

    # ---- Job Search ----
    # Multi-keyword: script will loop through each keyword to maximise applications.
    # The first keyword also uses 'custom_url' if set. Rest are auto-generated URLs.
    'search_keywords': [
        'Java',
        'Spring Boot',
    ],
    'search_keyword': 'Java',   # Fallback if search_keywords is empty

    'max_applications': 300,    # Total across all keywords in one run
    'jobs_per_keyword': 300,    # Let it apply to as many as possible on this link

    # Direct Naukri URL. 
    'custom_url': (
        'https://www.naukri.com/java-jobs?k=java&nignbevent_src=jobsearchDeskGNB'
    ),

    # ---- Experience ----
    'experience_years': 2,
    'notice_period': '30 Days',

    # ---- Location ----
    'current_location':   'Bangalore',
    'preferred_location': 'Bangalore',

    # ---- Compensation (in full rupees, not lakhs) ----
    'current_ctc':  '650000',
    'expected_ctc': '900000',

    # ---- Personal ----
    'date_of_birth': '11/06/2001',

    # ---- Filtering ----
    # Only Accenture is excluded — skipped BEFORE the job page is even opened.
    'excluded_companies': ['Accenture'],

    # If the same company appears more than this many times on a results page,
    # stop applying to it and move on (avoids spam to mass-posting agencies).
    'max_company_repetition': 5,

    # ---- Willingness defaults (used when AI can't determine from resume) ----
    'willing_to_relocate':   'Yes',
    'work_mode_preference':  'Hybrid',  # Remote / Hybrid / On-site
}
