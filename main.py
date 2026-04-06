
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('instahyre_automation.log'),
        logging.StreamHandler()
    ]
)

class InstahyreAutoApply:
    def __init__(self, url, xpath, target_clicks=25):
        self.url = url
        self.xpath = xpath
        self.target_clicks = target_clicks
        self.successful_clicks = 0
        self.failed_attempts = 0
        self.driver = None

    def setup_driver(self):
        """Initialize Chrome driver with options"""
        options = webdriver.ChromeOptions()
        options.add_argument('--start-maximized')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)

        # Automatic ChromeDriver management
        self.driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        self.wait = WebDriverWait(self.driver, 15)
        logging.info("Chrome driver initialized")

    def login_if_needed(self):
        """Wait for manual login if required"""
        self.driver.get(self.url)
        time.sleep(3)

        if "login" in self.driver.current_url.lower():
            logging.warning("⚠ Login required. Please login manually in the browser...")
            input("Press ENTER after you've logged in and can see job listings...")
            self.driver.get(self.url)
            time.sleep(3)

    def click_apply_button(self):
        """Find and click the apply button, wait for next job to load"""
        try:
            # Wait for apply button to be clickable
            apply_button = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, self.xpath))
            )

            # Scroll to button for visibility
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", apply_button)
            time.sleep(0.5)

            # Click the button
            try:
                apply_button.click()
            except ElementClickInterceptedException:
                # Use JavaScript click if normal click is intercepted
                self.driver.execute_script("arguments[0].click();", apply_button)

            self.successful_clicks += 1
            logging.info(f"✓ Applied to job #{self.successful_clicks}/{self.target_clicks}")

            # Wait for next job to load (adjust timing if needed)
            time.sleep(2)

            return True

        except TimeoutException:
            logging.error(f"✗ Timeout: Apply button not found (after {self.successful_clicks} successful clicks)")
            return False
        except Exception as e:
            logging.error(f"✗ Error: {str(e)}")
            return False

    def run(self):
        """Main execution method"""
        try:
            self.setup_driver()
            self.login_if_needed()

            print(f"\n{'='*60}")
            print(f"Starting Auto-Apply: Target = {self.target_clicks} applications")
            print(f"{'='*60}\n")

            while self.successful_clicks < self.target_clicks:
                success = self.click_apply_button()

                if not success:
                    self.failed_attempts += 1
                    logging.warning(f"Retrying... (Failed attempts: {self.failed_attempts})")
                    time.sleep(3)

                    # Stop if too many consecutive failures
                    if self.failed_attempts > 5:
                        logging.error("Too many failures. Possible reasons:")
                        logging.error("  - No more jobs available")
                        logging.error("  - Page structure changed")
                        logging.error("  - Network issue")
                        break
                else:
                    # Reset failure counter on success
                    self.failed_attempts = 0

            print(f"\n{'='*60}")
            print(f"✓ Automation Complete!")
            print(f"Total Applications Submitted: {self.successful_clicks}/{self.target_clicks}")
            print(f"{'='*60}\n")

        except KeyboardInterrupt:
            logging.warning(f"\n⚠ Interrupted by user. Applied to {self.successful_clicks} jobs.")

        except Exception as e:
            logging.error(f"Fatal error: {str(e)}")

        finally:
            if self.driver:
                input("\nPress ENTER to close browser...")
                self.driver.quit()

if __name__ == "__main__":
    
    URL = "https://www.instahyre.com/candidate/opportunities/?company_size=0&job_type=0&search=true&skills=Java&years=2"
    XPATH = '//*[@id="candidate-suggested-employers"]/div/div[3]/div/div/div[2]/div[3]/div[2]/div[2]/button'
    TARGET_CLICKS = 332

    # Run automation
    print("\n🤖 Instahyre Auto-Apply Bot Starting...\n")
    bot = InstahyreAutoApply(URL, XPATH, TARGET_CLICKS)
    bot.run()