"""
Auto-dismiss SonarQube-flagged PR issues as "Won't Fix" inside Azure DevOps.

CONTEXT: These issues show up as comment threads directly on the Azure DevOps
PR "Files" / "Overview" page (posted by a "SonarQube" or "Azure Devops"
service account), each with a state dropdown button like:

    <button aria-label="State button Active mode" aria-expanded="false"
            aria-haspopup="true" aria-controls="_bolt-dropdown-n-3" ...>
        <div class="bolt-dropdown-expandable-button-label">Active</div>
    </button>

Clicking it opens a menu (rendered into a container matching the
aria-controls id) with options like Active / Won't Fix / False Positive.

SETUP NOTES:
1. pip install selenium
2. Update PR_URL below to the actual Azure DevOps PR page URL (Files tab,
   since that's where these comment threads render).
3. Auth: Azure DevOps uses your org's SSO (Entra ID), so don't script the
   login. Launch Chrome with a persistent profile that's already logged in:
   - Close all Chrome windows.
   - Run once manually: chrome.exe --user-data-dir="C:\\selenium\\chrome-profile"
   - Log into Azure DevOps in that window, then close Chrome.
   - Point CHROME_PROFILE_DIR below at that same folder.
4. This only handles threads currently in "Active" state — already-dismissed
   ones ("Won't fix") are skipped automatically, so it's safe to re-run.
5. The page likely lazy-loads comments as you scroll, so this scrolls down
   periodically to reveal more threads before re-scanning.
"""

import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
from selenium.webdriver.common.keys import Keys

# ---- CONFIG ----
PR_URL = "https://dev.azure.com/TrygCloud/TH.Commercial.Policy/_git/rsa-batch-prosit/pullrequest/34761"
CHROME_PROFILE_DIR = r"C:\selenium\chrome-profile"  
MAX_ISSUES = 200  
WAIT_TIMEOUT = 10
TARGET_STATE_TEXT = "Won't fix"  

# ---- DRIVER SETUP ----
options = Options()
options.add_argument(f"--user-data-dir={CHROME_PROFILE_DIR}")
options.add_argument("--profile-directory=Default")  

options.add_argument("--disable-blink-features=AutomationControlled")
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option("useAutomationExtension", False)

driver = webdriver.Chrome(options=options)
wait = WebDriverWait(driver, WAIT_TIMEOUT)

skipped = set()  # selenium element ids of buttons that failed, so we don't loop on them

ACTIVE_BTN_XPATH = ("//button[@aria-haspopup='true' "
                    "and contains(@aria-label, 'State button Active')]")
ANY_STATE_BTN_XPATH = ("//button[@aria-haspopup='true' "
                       "and contains(@aria-label, 'State button')]")

def find_active_state_button():
    for b in driver.find_elements(By.XPATH, ACTIVE_BTN_XPATH):
        if b.id not in skipped:
            return b
    return None


def is_expanded(button):
    try:
        return button.get_attribute("aria-expanded") == "true"
    except StaleElementReferenceException:
        return False


def is_active(button):
    try:
        return "Active" in (button.get_attribute("aria-label") or "")
    except StaleElementReferenceException:
        return False  # re-rendered, treat as changed


def dismiss_one(button) -> bool:
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    time.sleep(0.3)

    button.click()
    try:
        WebDriverWait(driver, 3).until(lambda d: is_expanded(button))
    except TimeoutException:
        print("  native click didn't expand menu, retrying with JS click...")
        driver.execute_script("arguments[0].click();", button)
        try:
            WebDriverWait(driver, 3).until(lambda d: is_expanded(button))
        except TimeoutException:
            print("  menu still not expanded. Skipping.")
            return False

    controls_id = button.get_attribute("aria-controls")
    if not controls_id:
        print("  no aria-controls id. Skipping.")
        return False

    try:
        menu = wait.until(EC.visibility_of_element_located((By.ID, controls_id)))
        option = menu.find_element(
            By.XPATH, f".//*[contains(normalize-space(text()), \"{TARGET_STATE_TEXT}\")]"
        )
    except Exception as e:
        print(f"  couldn't open menu / find option: {type(e).__name__}")
        return False

    option.click()

    try:
        WebDriverWait(driver, 8).until(lambda d: not is_active(button))
    except TimeoutException:
        try:
            print(f"  state didn't change; label is: {button.get_attribute('aria-label')}")
        except StaleElementReferenceException:
            pass
        return False

    time.sleep(0.2)  # small settle for the re-render
    return True


def main():
    PR_URL = input("Enter PR URL: ")

    driver.get(PR_URL)

    print(
        "\nBrowser opened. Log in manually if prompted, then make sure the "
        "PR page with issue comment threads is visible.\n"
        "Waiting up to 5 minutes for the page to be ready — no need to switch "
        "back to this console, just finish logging in in the browser..."
    )

    long_wait = WebDriverWait(driver, 300)
    try:
        long_wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//button[@aria-haspopup='true' and contains(@aria-label, 'State button')]")
            )
        )
        print("Found issue threads on the page — starting.\n")
    except TimeoutException:
        print(
            "Timed out waiting for issue threads to appear. Either login took "
            "too long, or the page/selectors don't match what's expected. "
            "Exiting."
        )
        driver.quit()
        return

    processed = 0
    idle_scrolls = 0

    while processed < MAX_ISSUES:
        btn = find_active_state_button()

        if btn is None:
            # Nothing active in the loaded DOM: scroll the last thread into view
            # to trigger lazy loading, and stop only after several no-change scrolls.
            all_btns = driver.find_elements(By.XPATH, ANY_STATE_BTN_XPATH)
            if not all_btns:
                break
            before = (len(all_btns), all_btns[-1].id)
            driver.execute_script("arguments[0].scrollIntoView({block: 'end'});", all_btns[-1])
            time.sleep(1.2)
            after_btns = driver.find_elements(By.XPATH, ANY_STATE_BTN_XPATH)
            after = (len(after_btns), after_btns[-1].id) if after_btns else None
            idle_scrolls = idle_scrolls + 1 if after == before else 0
            if idle_scrolls >= 3:
                print("No more active issues found.")
                break
            continue

        idle_scrolls = 0
        try:
            success = dismiss_one(btn)
        except StaleElementReferenceException:
            continue  # re-render mid-action; just re-scan

        if success:
            processed += 1
            print(f"Dismissed issue #{processed}")
        else:
            skipped.add(btn.id)
            # close any open menu before moving on
            webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.3)

    print(f"Done. Total dismissed: {processed}")
    driver.quit()


if __name__ == "__main__":
    main()