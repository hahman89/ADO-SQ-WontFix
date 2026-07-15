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


def find_active_state_button():
    """Returns the first still-active (not yet Won't fix) state button found,
    or None if none are currently visible/loaded."""
    buttons = driver.find_elements(
        By.XPATH,
        "//button[@aria-haspopup='true' and contains(@aria-label, 'State button') "
        "and not(contains(@aria-label, \"Won't fix\"))]",
    )
    return buttons[0] if buttons else None


def dismiss_one(button) -> bool:
    """Clicks the state button, then clicks the target option in the menu
    it controls. Returns True on success."""
    
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    time.sleep(0.3)

    button.click()
    time.sleep(0.5)

    expanded = button.get_attribute("aria-expanded")
    controls_id = button.get_attribute("aria-controls")  
    print(f"  after click: aria-expanded={expanded}, aria-controls={controls_id}")

    if expanded != "true" or not controls_id:
        
        print("  native click didn't expand menu, retrying with JS click...")
        driver.execute_script("arguments[0].click();", button)
        time.sleep(0.5)
        expanded = button.get_attribute("aria-expanded")
        controls_id = button.get_attribute("aria-controls")
        print(f"  after JS click: aria-expanded={expanded}, aria-controls={controls_id}")

    if not controls_id:
        print("  still no aria-controls id — can't locate the menu. Skipping.")
        return False

    try:
        menu = wait.until(
            EC.visibility_of_element_located((By.ID, controls_id))
        )
    except TimeoutException:
        print(f"Menu with id '{controls_id}' never became visible.")
        # Dump what IS on the page with that id, if anything, for debugging.
        present = driver.find_elements(By.ID, controls_id)
        print(f"  elements matching id (any visibility): {len(present)}")
        if present:
            print(f"  outerHTML snippet: {present[0].get_attribute('outerHTML')[:300]}")
        return False

    try:
        option = menu.find_element(
            By.XPATH, f".//*[contains(normalize-space(text()), \"{TARGET_STATE_TEXT}\")]"
        )
    except Exception:
        print(f"Couldn't find '{TARGET_STATE_TEXT}' option in menu {controls_id}.")
        print(f"  menu outerHTML snippet: {menu.get_attribute('outerHTML')[:500]}")
        return False

    option.click()
    time.sleep(0.8) 
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
    stale_retries = 0

    while processed < MAX_ISSUES:
        btn = find_active_state_button()

        if btn is None:
            
            driver.execute_script("window.scrollBy(0, 1000);")
            time.sleep(1)
            btn_after_scroll = find_active_state_button()
            if btn_after_scroll is None:
                print("No more active issues found (after scroll check).")
                break
            btn = btn_after_scroll

        try:
            success = dismiss_one(btn)
        except StaleElementReferenceException:
            stale_retries += 1
            if stale_retries > 5:
                print("Too many stale element errors — stopping.")
                break
            continue

        if success:
            processed += 1
            stale_retries = 0
            print(f"Dismissed issue #{processed}")
        else:
            print("Stopping due to failed dismissal — inspect manually.")
            break

    print(f"Done. Total dismissed: {processed}")
    driver.quit()


if __name__ == "__main__":
    main()