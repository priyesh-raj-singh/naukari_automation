import logging
import time
import json
import os
from resume_parser import parse_resume
from config import GEMINI_API_KEY, RESUME_PATH, PERSONAL_INFO

CACHE_FILE = 'ai_answer_cache.json'

# Questions where "Yes" is definitely wrong — need specific answers
NEGATIVE_PATTERNS = [
    'ex-infy', 'ex infy', 'employee id', 'emp id', 'pan number', 'pan card',
    'passport', 'uan number', 'aadhar', 'aadhaar', 'reference number',
    'employee code', 'staff id', 'badge number', 'if yes, share', 'if yes, provide',
    'if yes, mention', 'ex-employee id', 'previous company id',
]

# Patterns that should always return "No"
NO_PATTERNS = [
    'did you work with infosys', 'are you an ex-infy', 'ex infosys',
    'do you have experience with sap', 'mainframe', 'cobol',
]

# CTC / salary helpers
LPA_KEYWORDS = ['lakh', 'lac', 'lpa', 'l.p.a', 'l pa', 'in lakhs', 'lakhs per']
EXPECTED_CTC_LPA = round(int(PERSONAL_INFO['expected_ctc']) / 100000, 1)
CURRENT_CTC_LPA  = round(int(PERSONAL_INFO['current_ctc'])  / 100000, 1)


class AIAgent:
    def __init__(self):
        self.resume_text  = None
        self.model        = None
        self.answer_cache = {}
        self.api_available = False
        self._skills_list  = []  # parsed from resume for quick lookup

        self._load_cache()
        self._load_resume()
        self._init_gemini()

    # ================================================================
    # Cache persistence
    # ================================================================

    def _load_cache(self):
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    self.answer_cache = json.load(f)
                logging.info(f"💾 Loaded {len(self.answer_cache)} cached answers from {CACHE_FILE}")
        except Exception as e:
            logging.warning(f"Could not load answer cache: {e}")
            self.answer_cache = {}

    def _save_cache(self):
        try:
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.answer_cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.warning(f"Could not save answer cache: {e}")

    # ================================================================
    # Resume & Gemini init
    # ================================================================

    def _load_resume(self):
        try:
            self.resume_text = parse_resume(RESUME_PATH)
            if self.resume_text:
                logging.info(f"✅ Resume loaded: {len(self.resume_text)} chars from {RESUME_PATH}")
                self._skills_list = self._extract_skills_from_resume()
            else:
                logging.warning(f"⚠️ Could not parse resume from {RESUME_PATH}")
        except Exception as e:
            logging.error(f"❌ Error loading resume: {e}")

    def _extract_skills_from_resume(self):
        """Build a flat list of skill tokens from resume text for fast lookup."""
        if not self.resume_text:
            return []
        # Common tech skills to look for
        common_skills = [
            'java', 'spring', 'spring boot', 'hibernate', 'jpa', 'microservices',
            'rest', 'restful', 'api', 'aws', 'docker', 'kubernetes', 'kafka',
            'rabbitmq', 'redis', 'mysql', 'postgresql', 'mongodb', 'react',
            'angular', 'javascript', 'typescript', 'python', 'git', 'jenkins',
            'ci/cd', 'maven', 'gradle', 'junit', 'mockito', 'sql', 'nosql',
            'linux', 'bash', 'html', 'css', 'node', 'express',
        ]
        text_lower = self.resume_text.lower()
        return [s for s in common_skills if s in text_lower]

    def _init_gemini(self):
        if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
            logging.warning("⚠️ No Gemini API key. Using fallback keyword matching.")
            return
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            self.model = genai.GenerativeModel('gemini-2.0-flash')
            self.api_available = True
            logging.info("✅ Gemini AI initialized (gemini-2.0-flash)")
        except ImportError:
            logging.error("❌ google-generativeai not installed. Run: pip install google-generativeai")
        except Exception as e:
            logging.error(f"❌ Error initializing Gemini: {e}")

    # ================================================================
    # Prompt building — now field-type and job-context aware
    # ================================================================

    def _build_prompt(self, question, field_type='text', options=None, job_context=None):
        personal_info_text = "\n".join([
            f"- Current Location: {PERSONAL_INFO['current_location']}",
            f"- Preferred Location: {PERSONAL_INFO['preferred_location']}",
            f"- Total Experience: {PERSONAL_INFO['experience_years']} years",
            f"- Current CTC: {PERSONAL_INFO['current_ctc']} INR/year ({CURRENT_CTC_LPA} LPA)",
            f"- Expected CTC: {PERSONAL_INFO['expected_ctc']} INR/year ({EXPECTED_CTC_LPA} LPA)",
            f"- Notice Period: {PERSONAL_INFO['notice_period']}",
            f"- Date of Birth: {PERSONAL_INFO['date_of_birth']}",
            f"- Willing to Relocate: {PERSONAL_INFO['willing_to_relocate']}",
            f"- Work Mode Preference: {PERSONAL_INFO['work_mode_preference']}",
            f"- Skills on resume: {', '.join(self._skills_list) if self._skills_list else 'See resume'}",
        ])

        resume_section = ""
        if self.resume_text:
            resume_section = f"\n=== CANDIDATE'S RESUME ===\n{self.resume_text[:3000]}\n=== END OF RESUME ===\n"

        job_ctx_section = ""
        if job_context:
            job_ctx_section = f"\n=== CURRENT JOB BEING APPLIED TO ===\n{job_context}\n"

        field_info = f"Field type: {field_type}"
        if options:
            field_info += f"\nAvailable options to choose from: {', '.join(options)}"

        prompt = f"""You are an AI assistant helping a job candidate fill out application forms on Naukri.com.

=== CANDIDATE'S PERSONAL INFO ===
{personal_info_text}
{resume_section}{job_ctx_section}

=== FIELD INFO ===
{field_info}

=== STRICT RULES ===
1. Give ONLY the answer value. No explanation, no quotes, no punctuation at the end.
2. For yes/no questions: answer "Yes" or "No" only.
3. For numeric fields (experience, CTC): give JUST the number.
4. For CTC: if the label or context mentions "lakh/lac/lpa", give the LPA value (e.g. {EXPECTED_CTC_LPA}).
   Otherwise give the full rupee value (e.g. {PERSONAL_INFO['expected_ctc']}).
5. For location: city name only.
6. For notice period: match the available options if given, otherwise "{PERSONAL_INFO['notice_period']}".
7. For open-ended questions (reason for change, strengths, why us): max 10 words, growth-oriented.
8. For date of birth: use DD/MM/YYYY format, answer is "{PERSONAL_INFO['date_of_birth']}".
9. For skill experience questions: ONLY say "Yes" or give years if the skill is IN the resume.
   If the skill is NOT in the resume, say "No" or "0". Do NOT fabricate experience.
10. For "are you willing to..." questions: "Yes".
11. For gender: "Male".
12. If options are provided, your answer MUST exactly match one of the given options.
13. For questions about previous company IDs, employee codes, PAN, passport — answer "NA".
14. For questions about companies you haven't worked at (ex-Infosys, ex-TCS etc.) — answer "No".

=== QUESTION ===
{question}

=== YOUR ANSWER ==="""
        return prompt

    # ================================================================
    # CTC auto-conversion (no AI needed)
    # ================================================================

    def _handle_ctc_question(self, question, label_context=''):
        """Detect CTC questions and return the right format without an API call."""
        q = question.lower()
        ctx = (q + ' ' + label_context.lower())

        is_expected = any(w in ctx for w in ['expected', 'desired', 'asking'])
        is_current  = any(w in ctx for w in ['current', 'present', 'last drawn', 'annual ctc'])
        is_lpa      = any(w in ctx for w in LPA_KEYWORDS)

        if is_expected:
            return str(EXPECTED_CTC_LPA) if is_lpa else PERSONAL_INFO['expected_ctc']
        if is_current:
            return str(CURRENT_CTC_LPA) if is_lpa else PERSONAL_INFO['current_ctc']
        return None

    # ================================================================
    # Public: answer a question — now accepts field metadata
    # ================================================================

    def answer_question(self, question, field_type='text', options=None, job_context=None):
        """
        Answer a job application question.

        Args:
            question:     The question or label text
            field_type:   'text' | 'number' | 'select' | 'radio' | 'checkbox'
            options:      List of visible option strings (for select/radio)
            job_context:  Short string like "Job: Java Developer at Infosys"
        """
        if not question or len(question.strip()) < 2:
            return "Yes"

        question   = question.strip()
        cache_key  = f"{question.lower().strip()}|{field_type}|{','.join(options or [])}"

        # ---- Fast-path: negative / dangerous question patterns ----
        q_lower = question.lower()

        if any(pat in q_lower for pat in NO_PATTERNS):
            logging.info(f"    ⛔ Pattern match → 'No': '{question[:50]}'")
            return "No"

        if any(pat in q_lower for pat in NEGATIVE_PATTERNS):
            logging.info(f"    ⛔ Sensitive field → 'NA': '{question[:50]}'")
            return "NA"

        # ---- Fast-path: CTC without API ----
        ctc_ans = self._handle_ctc_question(question)
        if ctc_ans:
            logging.info(f"    💰 CTC match → '{ctc_ans}': '{question[:50]}'")
            return ctc_ans

        # ---- Cache hit ----
        if cache_key in self.answer_cache:
            cached = self.answer_cache[cache_key]
            logging.info(f"    💾 Cache hit: '{question[:40]}' → '{cached}'")
            return cached

        # ---- Legacy cache hit (old keys without field_type suffix) ----
        old_key = question.lower().strip()
        if old_key in self.answer_cache:
            cached = self.answer_cache[old_key]
            logging.info(f"    💾 Legacy cache: '{question[:40]}' → '{cached}'")
            return cached

        # ---- Try Gemini AI ----
        if self.api_available and self.model:
            try:
                answer = self._ask_gemini(question, field_type, options, job_context)
                if answer:
                    # Validate against options if provided
                    if options and field_type in ('select', 'radio'):
                        answer = self._match_to_options(answer, options)
                    self.answer_cache[cache_key] = answer
                    self._save_cache()
                    logging.info(f"    🤖 AI answer: '{question[:40]}' → '{answer}'")
                    return answer
            except Exception as e:
                logging.warning(f"    ⚠️ Gemini error: {str(e)[:100]}")

        # ---- Fallback ----
        answer = self._keyword_fallback(question, field_type, options)
        self.answer_cache[cache_key] = answer
        self._save_cache()
        logging.info(f"    📋 Fallback answer: '{question[:40]}' → '{answer}'")
        return answer

    def _match_to_options(self, ai_answer, options):
        """Match AI answer to the closest available option."""
        ai_lower = ai_answer.lower()
        # Exact match
        for opt in options:
            if opt.lower() == ai_lower:
                return opt
        # Partial match
        for opt in options:
            if ai_lower in opt.lower() or opt.lower() in ai_lower:
                return opt
        # Number match (e.g. AI says "30" and option is "30 Days")
        for opt in options:
            if any(c.isdigit() for c in ai_answer):
                digits = ''.join(filter(str.isdigit, ai_answer))
                if digits in opt:
                    return opt
        # Fallback: return AI answer unchanged, caller will handle
        return ai_answer

    def _ask_gemini(self, question, field_type='text', options=None, job_context=None):
        prompt = self._build_prompt(question, field_type, options, job_context)
        try:
            response = self.model.generate_content(prompt)
            if response and response.text:
                answer = response.text.strip().strip('"').strip("'").rstrip('.').split('\n')[0].strip()
                return answer[:100] if len(answer) > 100 else answer if answer else None
        except Exception as e:
            if 'quota' in str(e).lower() or 'rate' in str(e).lower():
                logging.warning("    ⚠️ Rate limit — waiting 5s...")
                time.sleep(5)
                try:
                    response = self.model.generate_content(prompt)
                    if response and response.text:
                        return response.text.strip().strip('"').strip("'").rstrip('.').split('\n')[0].strip()
                except Exception:
                    pass
            raise
        return None

    # ================================================================
    # Keyword fallback — smarter separation of yes/no vs text
    # ================================================================

    def _keyword_fallback(self, question, field_type='text', options=None):
        q = question.lower()

        # CTC (covered above but keep as safety net)
        if any(w in q for w in ['current ctc', 'current salary', 'present ctc', 'last drawn']):
            return PERSONAL_INFO['current_ctc']
        if any(w in q for w in ['expected ctc', 'expected salary', 'desired salary']):
            return PERSONAL_INFO['expected_ctc']

        # Experience
        if any(w in q for w in ['years of experience', 'how many years', 'total experience']):
            return str(PERSONAL_INFO['experience_years'])

        # Notice period — match to options if available
        if any(w in q for w in ['notice', 'joining', 'available to join']):
            if options:
                return self._match_to_options(PERSONAL_INFO['notice_period'], options)
            return PERSONAL_INFO['notice_period']

        # Location
        if any(w in q for w in ['current location', 'current city', 'residing', 'where are you']):
            return PERSONAL_INFO['current_location']
        if any(w in q for w in ['preferred location', 'preferred city', 'relocate to']):
            return PERSONAL_INFO['preferred_location']
        if any(w in q for w in ['location', 'city']):
            return PERSONAL_INFO['current_location']

        # DOB
        if any(w in q for w in ['date of birth', 'dob', 'birth date', 'birthday']):
            return PERSONAL_INFO['date_of_birth']

        # Gender
        if any(w in q for w in ['gender', 'sex']):
            return 'Male'

        # Skills — honest answer based on resume
        skill_keywords = ['experience with', 'experience in', 'do you have', 'have you worked',
                          'how many years', 'proficiency in', 'knowledge of']
        if any(w in q for w in skill_keywords) and self._skills_list:
            for skill in self._skills_list:
                if skill in q:
                    return str(PERSONAL_INFO['experience_years'])
            # Skill not found in resume → honest "No" for yes/no fields
            if field_type in ('radio', 'checkbox') or any(
                w in q for w in ['do you', 'have you', 'are you', 'can you']
            ):
                return 'No'

        # Yes/no questions where "Yes" is genuinely correct
        if any(w in q for w in ['willing', 'comfortable', 'agree', 'relocate',
                                  'remote', 'hybrid', 'hike', 'open to']):
            return 'Yes'

        # Free text fields — never default to "Yes"
        if field_type in ('text', 'number', 'textarea'):
            if any(w in q for w in ['why', 'reason', 'describe', 'explain', 'tell us']):
                return 'Seeking better growth opportunities'
            if field_type == 'number':
                return str(PERSONAL_INFO['experience_years'])

        # Final fallback: Yes for radio/checkbox, empty-ish for text
        if field_type in ('radio', 'checkbox'):
            return 'Yes'

        return 'Yes'

    # ================================================================
    # Dropdown-specific helper: pick best option with AI or keywords
    # ================================================================

    def pick_dropdown_option(self, label, options, job_context=None):
        """
        Smart dropdown option picker.
        Always passes the full options list so AI can return an exact match.
        """
        if not options:
            return None

        # Filter out placeholder options
        real_options = [o for o in options if not any(
            p in o.lower() for p in ['select', 'choose', '---', 'please', 'pick']
        )]
        if not real_options:
            return options[1] if len(options) > 1 else options[0]

        # Fast path: notice period
        if any(w in label.lower() for w in ['notice', 'joining', 'available']):
            matched = self._match_to_options(PERSONAL_INFO['notice_period'], real_options)
            if matched in real_options:
                return matched
            # Fuzzy: find option with "30" in it
            for opt in real_options:
                if '30' in opt:
                    return opt
            return real_options[0]

        # Fast path: current/expected CTC
        ctc_ans = self._handle_ctc_question(label)
        if ctc_ans:
            matched = self._match_to_options(ctc_ans, real_options)
            if matched in real_options:
                return matched

        # Fast path: location
        if any(w in label.lower() for w in ['location', 'city', 'relocate']):
            matched = self._match_to_options(PERSONAL_INFO['current_location'], real_options)
            if matched in real_options:
                return matched

        # AI path
        return self.answer_question(label, field_type='select', options=real_options,
                                    job_context=job_context)

    def get_status(self):
        status = []
        if self.resume_text:
            status.append(f"📄 Resume: {len(self.resume_text)} chars")
        else:
            status.append("⚠️ Resume: Not loaded")
        status.append("🤖 AI: Gemini active" if self.api_available else "📋 AI: Fallback mode")
        status.append(f"💾 Cache: {len(self.answer_cache)} answers")
        status.append(f"🔧 Skills detected: {len(self._skills_list)}")
        return " | ".join(status)


# ============ STANDALONE TEST ============
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

    print("\n" + "=" * 60)
    print("🤖 AI AGENT TEST")
    print("=" * 60)

    agent = AIAgent()
    print(f"\nStatus: {agent.get_status()}")

    test_cases = [
        # (question, field_type, options)
        ("What is your current CTC?",               'text',   None),
        ("Expected CTC in lakhs?",                  'number', None),
        ("What is your notice period?",             'select', ['Immediate', '15 Days', '30 Days', '60 Days', '90 Days']),
        ("Current location?",                       'text',   None),
        ("Years of experience in Java?",            'number', None),
        ("Years of experience in SAP?",             'number', None),
        ("Are you willing to relocate?",            'radio',  ['Yes', 'No']),
        ("Did you work with Infosys?",              'radio',  ['Yes', 'No']),
        ("If yes, share your ex-Infy ID",           'text',   None),
        ("What is your date of birth?",             'text',   None),
        ("Why are you looking for a change?",       'text',   None),
        ("Do you have Spring Boot experience?",     'radio',  ['Yes', 'No']),
        ("Do you have COBOL experience?",           'radio',  ['Yes', 'No']),
        ("What is your gender?",                    'select', ['Male', 'Female', 'Prefer not to say']),
    ]

    print(f"\nTesting {len(test_cases)} cases:\n" + "-" * 60)
    for q, ft, opts in test_cases:
        answer = agent.answer_question(q, field_type=ft, options=opts,
                                       job_context="Job: Java Developer at TCS, Bangalore")
        opts_str = f" [options: {opts}]" if opts else ""
        print(f"Q ({ft}): {q}{opts_str}")
        print(f"A: {answer}")
        print("-" * 60)

    print(f"\nFinal Status: {agent.get_status()}")