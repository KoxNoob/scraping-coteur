import streamlit as st
import gspread
import pandas as pd
import json
import re
import time
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.firefox import GeckoDriverManager


# 📌 Function to initialize Selenium
def init_driver():
    firefox_options = Options()
    firefox_options.add_argument("--headless")  # Headless mode required for Streamlit Cloud
    firefox_options.add_argument("--no-sandbox")
    firefox_options.add_argument("--disable-dev-shm-usage")

    service = Service(GeckoDriverManager().install())
    driver = webdriver.Firefox(service=service, options=firefox_options)
    return driver


# 📌 Function to retrieve competitions from Google Sheets
def get_competitions_from_sheets(sheet_name):
    credentials_dict = st.secrets["GOOGLE_SHEET_CREDENTIALS"]
    credentials = Credentials.from_service_account_info(
        credentials_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    client = gspread.authorize(credentials)

    SPREADSHEET_ID = "16ZBhF4k4ah-zhc3QcH7IEWLXrhbT8TRTMi5BptCFIcM"
    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)
    data = sheet.get_all_records()
    competitions_df = pd.DataFrame(data)

    required_columns = {"Pays", "Compétition", "URL"}
    if not required_columns.issubset(competitions_df.columns):
        st.error(f"❌ The Google Sheet does not contain the required columns: {required_columns}")
        return pd.DataFrame()

    competitions_df = competitions_df.sort_values(
        by=["Pays", "Compétition"],
        key=lambda x: x.map(lambda y: ("" if y == "France" else y))
    )
    return competitions_df


# 📌 Function to scrape betting odds for a competition
def get_match_odds(competition_url, selected_bookmakers, nb_matchs, is_tennis=False):
    driver = init_driver()
    driver.get(competition_url)

    try:
        WebDriverWait(driver, 5).until(
            EC.presence_of_all_elements_located((By.TAG_NAME, "script"))
        )
    except:
        st.warning(f"⚠️ No matches found for {competition_url}")
        return pd.DataFrame()

    scripts = driver.find_elements(By.TAG_NAME, "script")
    match_links = []

    for script in scripts:
        if '"@type":"SportsEvent"' in script.get_attribute("innerHTML"):
            try:
                json_data = json.loads(re.sub(r'[\x00-\x1F\x7F]', '', script.get_attribute("innerHTML")))
                if isinstance(json_data, dict) and "url" in json_data:
                    original_url = "https://www.coteur.com" + json_data["url"]
                    corrected_url = original_url.replace("/match/pronostic-", "/cote/")
                    match_links.append(corrected_url)
            except json.JSONDecodeError:
                continue

    match_links = match_links[:nb_matchs]
    all_odds = []

    for match_url in match_links:
        driver.get(match_url)

        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.bookline"))
            )
        except:
            st.warning(f"⚠️ No odds found for {match_url}")
            continue

        time.sleep(2)
        driver.refresh()
        time.sleep(2)

        odds_script = '''
        let oddsData = [];
        document.querySelectorAll("div.bookline").forEach(row => {
            let bookmaker = row.getAttribute("data-name");
            let odds = row.querySelectorAll("div.odds-col");
            let payoutElem = row.querySelector("div.border.bg-warning.payout");
            let payout = payoutElem ? payoutElem.innerText.trim() : "N/A";

            if (odds.length >= 2) {
                let odd_1 = odds[0].innerText.trim();
                let odd_2 = odds[1].innerText.trim();
                oddsData.push([bookmaker, odd_1, odd_2, payout]);
            }
        });
        return oddsData;
        '''

        odds_list = driver.execute_script(odds_script)
        match_name = match_url.split("/")[-1].replace("-", " ").title()
        match_name = re.sub(r'\s*\d+#Cote\s*$', '', match_name).strip()

        for odd in odds_list:
            if odd[0] in selected_bookmakers:
                all_odds.append([match_name] + odd)

    driver.quit()
    return pd.DataFrame(all_odds, columns=["Match", "Bookmaker", "1", "2", "Payout"])


# 📌 Streamlit main interface
def main():
    st.set_page_config(page_title="Betting Odds Scraper", page_icon="⚽", layout="wide")

    st.sidebar.title("📌 Menu")
    menu_selection = st.sidebar.radio("Choose a mode", ["🏠 Home", "⚽ Football", "🎾 Tennis", "🔑 Admin"])

    if menu_selection in ["⚽ Football", "🎾 Tennis"]:
        sport = "Football" if menu_selection == "⚽ Football" else "Tennis"
        st.title(f"📊 {sport} Betting Odds Scraper")

        with st.spinner(f"🔄 Loading {sport} competitions from Google Sheets..."):
            competitions_df = get_competitions_from_sheets(sport)

        if competitions_df.empty:
            st.warning(f"⚠️ No {sport} competition data found in Google Sheets.")
        else:
            selected_competitions = st.multiselect(f"📌 Select {sport} competitions",
                                                   competitions_df["Compétition"].tolist())

            if selected_competitions:
                all_bookmakers = ["Winamax", "Unibet", "Betclic", "Pmu", "ParionsSport", "Zebet", "Olybet", "Bwin",
                                  "Vbet", "Genybet", "Feelingbet", "Betsson"]
                selected_bookmakers = st.multiselect("🎰 Select bookmakers", all_bookmakers, default=all_bookmakers)
                nb_matchs = st.slider("🔢 Number of matches per competition", 1, 20, 5)

                if st.button("🔍 Start scraping"):
                    with st.spinner("Scraping in progress..."):
                        all_odds_df = pd.concat([
                            get_match_odds(
                                competitions_df.loc[competitions_df["Compétition"] == comp, "URL"].values[0],
                                selected_bookmakers, nb_matchs, is_tennis=(sport == "Tennis")
                            ) for comp in selected_competitions
                        ], ignore_index=True)

                    if not all_odds_df.empty:
                        st.subheader(f"📌 Retrieved {sport} Odds")
                        st.dataframe(all_odds_df)


if __name__ == "__main__":
    main()
