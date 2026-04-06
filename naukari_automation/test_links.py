from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
from config import PERSONAL_INFO

def build_search_urls(profile):
    urls = []
    exp = profile['experience_years']
    exp_min = max(0, exp - 1)
    exp_max = exp + 2
    keywords = profile.get('search_keywords', [profile.get('search_keyword', 'Java')])

    custom_url = profile.get('custom_url', '').strip()
    if custom_url:
        if 'jobAge' not in custom_url: custom_url += '&jobAge=3'
        if 'sort=' not in custom_url: custom_url += '&sort=1'
        if 'experience=' not in custom_url: custom_url += f'&experience={exp_min},{exp_max}'
        if 'salary=' not in custom_url: custom_url += '&salary=4,15'
        urls.append(custom_url)
        remaining_keywords = keywords[1:]
    else:
        remaining_keywords = keywords

    for kw in remaining_keywords:
        kw_slug = kw.replace(' ', '-').lower()
        kw_encoded = kw.replace(' ', '+')
        urls.append(
            f"https://www.naukri.com/{kw_slug}-jobs"
            f"?k={kw_encoded}"
            f"&jobAge=3&sort=1"
            f"&experience={exp_min},{exp_max}"
            f"&salary=4,15"
        )
    return urls

urls = build_search_urls(PERSONAL_INFO)
print("Testing URL:", urls[0])

options = webdriver.ChromeOptions()
options.add_argument('--headless')
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

try:
    driver.get(urls[0])
    time.sleep(5)
    print("Current URL after load:", driver.current_url)

    elements = driver.find_elements(By.XPATH, "//a[contains(@class, 'title')]")
    if not elements:
        print("No elements found with class 'title'")
        
        # Dump standard classes for links on page
        all_links = driver.find_elements(By.TAG_NAME, "a")
        found = False
        for a in all_links[:30]:
            href = a.get_attribute('href')
            text = a.text.strip()
            if href and '-jobs-' in href:
                print(f"Sample Job Link -> href: {href}, text: {text}, class: {a.get_attribute('class')}")
                found = True
        
        if not found:
            print("Could not find any obvious job links. Page title:", driver.title)
    else:
        for idx, elem in enumerate(elements[:5]):
            print(f"Link {idx+1}: {elem.get_attribute('href')}")
            
finally:
    driver.quit()
