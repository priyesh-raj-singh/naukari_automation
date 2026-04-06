
import logging
import time
import json
import os
from resume_parser import parse_resume
from config import GEMINI_API_KEY, RESUME_PATH, PERSONAL_INFO

CACHE_FILE = 'ai_answer_cache.json'


class AIAgent:
    def __init__(self):
        self.resume_text = None
        self.model = None
        self.answer_cache = {}  # In-memory + persisted to disk
        self.api_available = False

        # Load persisted answer cache from previous runs
        self._load_cache()

        # Load resume
        self._load_resume()

        # Initialize Gemini
        self._init_gemini()

    # ================================================================
    # Cache persistence
    # ================================================================

    def _load_cache(self):
        """Load answer cache from disk (survives script restarts)."""
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    self.answer_cache = json.load(f)
                logging.info(f"💾 Loaded {len(self.answer_cache)} cached answers from {CACHE_FILE}")
        except Exception as e:
            logging.warning(f"Could not load answer cache: {e}")
            self.answer_cache = {}

    def _save_cache(self):
        """Persist answer cache to disk."""
        try:
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.answer_cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.warning(f"Could not save answer cache: {e}")

    # ================================================================
    # Resume & Gemini init
    # ================================================================

    def _load_resume(self):
        """Parse and load the resume."""
        try:
            self.resume_text = parse_resume(RESUME_PATH)
            if self.resume_text:
                logging.info(f"✅ Resume loaded: {len(self.resume_text)} chars from {RESUME_PATH}")
            else:
                logging.warning(f"⚠️ Could not parse resume from {RESUME_PATH}")
        except Exception as e:
            logging.error(f"❌ Error loading resume: {e}")

    def _init_gemini(self):
        """Initialize the Gemini model."""
        if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
            logging.warning("⚠️ No Gemini API key set. Using fallback keyword matching.")
            logging.warning("   Get your key at: https://aistudio.google.com/apikey")
            logging.warning("   Then set GEMINI_API_KEY in your .env file")
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
    # Prompt building
    # ================================================================

    def _build_prompt(self, question):
        """Build the prompt for Gemini with resume + profile context."""
        personal_info_text = "\n".join([
            f"- Current Location: {PERSONAL_INFO['current_location']}",
            f"- Preferred Location: {PERSONAL_INFO['preferred_location']}",
            f"- Total Experience: {PERSONAL_INFO['experience_years']} years",
            f"- Current CTC: {PERSONAL_INFO['current_ctc']} (Indian Rupees per annum)",
            f"- Expected CTC: {PERSONAL_INFO['expected_ctc']} (Indian Rupees per annum)",
            f"- Notice Period: {PERSONAL_INFO['notice_period']}",
            f"- Date of Birth: {PERSONAL_INFO['date_of_birth']}",
            f"- Willing to Relocate: {PERSONAL_INFO['willing_to_relocate']}",
            f"- Work Mode Preference: {PERSONAL_INFO['work_mode_preference']}",
        ])

        resume_section = ""
        if self.resume_text:
            resume_section = f"""
=== CANDIDATE'S RESUME ===
{self.resume_text}
=== END OF RESUME ===
"""

        prompt = f"""You are an AI assistant helping a job candidate fill out application forms on Naukri.com (Indian job portal).

Your job is to read the question and give the BEST answer based on the candidate's resume and personal info.

=== CANDIDATE'S PERSONAL INFO ===
{personal_info_text}

{resume_section}

=== RULES ===
1. Give ONLY the answer value. No explanations, no extra text, no quotes.
2. Keep answers professional and concise.
3. For standard form inputs (numeric, Yes/No, locations, dates), keep it to 1-3 words.
4. For open-ended or conversational questions (e.g., "Why are you looking?", "Reason for change"), provide a very concise, growth-oriented answer (max 10 words). Example: "Seeking better growth opportunities".
5. For yes/no questions, answer "Yes" or "No".
6. For numeric questions (CTC, experience, etc.), give JUST the number.
7. For CTC questions: if they ask in lakhs, convert accordingly (e.g., 650000 = 6.5).
   If they ask in rupees or don't specify, give the number as-is.
8. For location questions, answer with the city name only.
9. For date of birth, use the format they seem to expect (DD/MM/YYYY or MM/DD/YYYY).
   Default: 11/06/2001
10. For technology/skill questions, check the resume. If the skill is mentioned, say "Yes" or give
    the years of experience. If NOT mentioned, still say "Yes" with minimum experience (1 year).
11. For "are you willing to..." questions, answer "Yes".
12. If the question asks about gender, answer "Male".
13. For questions you truly cannot determine, answer "Yes" for yes/no or give a reasonable default.

=== QUESTION ===
{question}

=== YOUR ANSWER (just the value, nothing else) ==="""

        return prompt

    # ================================================================
    # Public: answer a question
    # ================================================================

    def answer_question(self, question):
        """
        Answer a job application question using AI.
        Falls back to keyword matching if AI is unavailable.
        Results are cached in memory AND persisted to disk.
        """
        if not question or len(question.strip()) < 2:
            return "Yes"

        question = question.strip()
        cache_key = question.lower().strip()

        # 1. Cache hit (fast path — no API call)
        if cache_key in self.answer_cache:
            cached = self.answer_cache[cache_key]
            logging.info(f"    💾 Cache hit: '{question[:40]}' → '{cached}'")
            return cached

        # 2. Try Gemini AI
        if self.api_available and self.model:
            try:
                answer = self._ask_gemini(question)
                if answer:
                    self.answer_cache[cache_key] = answer
                    self._save_cache()           # Persist immediately
                    logging.info(f"    🤖 AI answer: '{question[:40]}' → '{answer}'")
                    return answer
            except Exception as e:
                logging.warning(f"    ⚠️ Gemini API error: {str(e)[:100]}")

        # 3. Fallback to keyword matching
        answer = self._keyword_fallback(question)
        self.answer_cache[cache_key] = answer
        self._save_cache()
        logging.info(f"    📋 Fallback answer: '{question[:40]}' → '{answer}'")
        return answer

    def _ask_gemini(self, question):
        """Call Gemini API to answer the question."""
        prompt = self._build_prompt(question)

        try:
            response = self.model.generate_content(prompt)

            if response and response.text:
                answer = response.text.strip()
                answer = answer.strip('"').strip("'")
                answer = answer.rstrip('.')
                answer = answer.split('\n')[0].strip()

                if len(answer) > 100:
                    answer = answer[:100]

                return answer if answer else None

        except Exception as e:
            error_msg = str(e)
            if 'quota' in error_msg.lower() or 'rate' in error_msg.lower():
                logging.warning("    ⚠️ API rate limit hit, waiting 5 seconds...")
                time.sleep(5)
                try:
                    response = self.model.generate_content(prompt)
                    if response and response.text:
                        return response.text.strip().strip('"').strip("'").rstrip('.').split('\n')[0].strip()
                except:
                    pass
            raise

        return None

    def _keyword_fallback(self, question):
        """Fallback keyword matching when AI is not available."""
        q = question.lower()

        if any(w in q for w in ['current ctc', 'current salary', 'present ctc', 'last drawn', 'annual ctc']):
            return PERSONAL_INFO['current_ctc']
        if any(w in q for w in ['expected ctc', 'expected salary', 'desired salary']):
            return PERSONAL_INFO['expected_ctc']
        if any(w in q for w in ['notice', 'joining', 'available to join']):
            return PERSONAL_INFO['notice_period']
        if any(w in q for w in ['years', 'experience', 'how many']):
            return str(PERSONAL_INFO['experience_years'])
        if any(w in q for w in ['current location', 'current city', 'residing']):
            return PERSONAL_INFO['current_location']
        if any(w in q for w in ['preferred location', 'preferred city', 'relocate to']):
            return PERSONAL_INFO['preferred_location']
        if any(w in q for w in ['location', 'city']):
            return PERSONAL_INFO['current_location']
        if any(w in q for w in ['date of birth', 'dob', 'birth date', 'birthday']):
            return PERSONAL_INFO['date_of_birth']
        if any(w in q for w in ['gender', 'sex']):
            return 'Male'
        if any(w in q for w in ['comfortable', 'willing', 'relocate', 'agree',
                                  'remote', 'hybrid', 'office', 'work from', 'hike']):
            return 'Yes'

        return 'Yes'

    def get_status(self):
        """Return current status for display."""
        status = []
        if self.resume_text:
            status.append(f"📄 Resume: {len(self.resume_text)} chars loaded")
        else:
            status.append("⚠️ Resume: Not loaded")

        if self.api_available:
            status.append("🤖 AI: Gemini active")
        else:
            status.append("📋 AI: Fallback mode (no API key)")

        status.append(f"💾 Cache: {len(self.answer_cache)} answers cached")
        return " | ".join(status)


# ============ STANDALONE TEST ============
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

    print("\n" + "=" * 60)
    print("🤖 AI AGENT TEST")
    print("=" * 60)

    agent = AIAgent()
    print(f"\nStatus: {agent.get_status()}")

    test_questions = [
        "What is your current CTC?",
        "What is your expected CTC?",
        "What is your current location?",
        "What is your preferred location?",
        "What is your date of birth?",
        "How many years of experience do you have in Java?",
        "What is your notice period?",
        "Are you comfortable working in hybrid mode?",
        "Do you have experience with Spring Boot?",
        "Are you willing to relocate to Hyderabad?",
        "What is your highest qualification?",
        "Do you have experience with microservices architecture?",
    ]

    print(f"\nTesting {len(test_questions)} questions:\n")
    print("-" * 60)

    for q in test_questions:
        answer = agent.answer_question(q)
        print(f"Q: {q}")
        print(f"A: {answer}")
        print("-" * 60)

    print(f"\nFinal Status: {agent.get_status()}")
