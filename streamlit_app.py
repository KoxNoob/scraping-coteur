import streamlit as st
import gspread
import pandas as pd
import json
import re
import time
import os
import shutil
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.firefox import GeckoDriverManager


os.environ["GH_TOKEN"] = st.secrets["GH_TOKEN"]
shutil.rmtree("/home/adminuser/.wdm", ignore_errors=True)  # Supprime le cache WebDriver



# 📌 Function to initialize Selenium
def init_driver():
    firefox_options = Options()
    firefox_options.add_argument("--headless")  # Headless mode required for Streamlit Cloud
    firefox_options.add_argument("--no-sandbox")
    firefox_options.add_argument("--disable-dev-shm-usage")

    # ✅ Forcer un chemin valide pour le cache de webdriver-manager
    cache_path = "/tmp/webdriver_manager"
    if os.path.exists(cache_path):
        shutil.rmtree(cache_path)  # Supprime le cache existant
    os.makedirs(cache_path, exist_ok=True)  # Assure que le répertoire est recréé
    os.environ["WDM_LOCAL"] = "1"  # Force l'utilisation du cache local
    os.environ["WDM_CACHE"] = cache_path  # Définit le cache WebDriver

    # ✅ Télécharger et installer Geckodriver proprement
    gecko_path = GeckoDriverManager().install()
    service = Service(gecko_path)

    driver = webdriver.Firefox(service=service, options=firefox_options)
    return driver

# 📌 Function to retrieve competitions from Google Sheets
def get_competitions_from_sheets():
    # ✅ Load credentials from Streamlit secrets
    credentials_dict = st.secrets["GOOGLE_SHEET_CREDENTIALS"]
    credentials = Credentials.from_service_account_info(
        credentials_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    client = gspread.authorize(credentials)

    # 🔗 Google Sheet ID and worksheet name
    SPREADSHEET_ID = "16ZBhF4k4ah-zhc3QcH7IEWLXrhbT8TRTMi5BptCFIcM"
    SHEET_NAME = "Football"

    sheet = client.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    data = sheet.get_all_records()
    competitions_df = pd.DataFrame(data)

    required_columns = {"Pays", "Compétition", "URL"}
    if not required_columns.issubset(competitions_df.columns):
        st.error("❌ The Google Sheet does not contain the required columns: Pays, Compétition, URL")
        return pd.DataFrame()

    competitions_df = competitions_df.sort_values(
        by=["Pays", "Compétition"],
        key=lambda x: x.map(lambda y: ("" if y == "France" else y))
    )

    return competitions_df

# 📌 Function to scrape betting odds for a competition
def get_match_odds(competition_url, selected_bookmakers, nb_matchs):
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

            if (odds.length >= 3) {
                let odd_1 = odds[0].innerText.trim();
                let odd_n = odds[1].innerText.trim();
                let odd_2 = odds[2].innerText.trim();
                oddsData.push([bookmaker, odd_1, odd_n, odd_2, payout]);
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
    return pd.DataFrame(all_odds, columns=["Match", "Bookmaker", "1", "Draw", "2", "Payout"])

# 📌 Streamlit main interface
def main():
    st.set_page_config(page_title="Betting Odds Scraper", page_icon="⚽", layout="wide")

    st.sidebar.title("📌 Menu")
    menu_selection = st.sidebar.radio("Choose a mode", ["🏠 Home", "⚽ Football", "🔑 Admin"])

    if menu_selection == "⚽ Football":
        st.title("📊 Football Betting Odds Scraper")

        with st.spinner("🔄 Loading competitions from Google Sheets..."):
            competitions_df = get_competitions_from_sheets()

        if competitions_df.empty:
            st.warning("⚠️ No competition data found in Google Sheets.")
        else:
            st.session_state["competitions_df"] = competitions_df

            selected_competitions = st.multiselect("📌 Select competitions",
                                                   competitions_df["Compétition"].tolist())

            if selected_competitions:
                all_bookmakers = ["Winamax", "Unibet", "Betclic", "Pmu", "ParionsSport", "Zebet", "Olybet", "Bwin",
                                  "Vbet", "Genybet", "Feelingbet", "Betsson"]

                selected_bookmakers = st.multiselect("🎰 Select bookmakers", all_bookmakers,
                                                     default=all_bookmakers)
                nb_matchs = st.slider("🔢 Number of matches per competition", 1, 20, 5)

                if st.button("🔍 Start scraping"):
                    with st.spinner("Scraping in progress..."):
                        all_odds_df = pd.concat([
                            get_match_odds(
                                competitions_df.loc[competitions_df["Compétition"] == comp, "URL"].values[0],
                                selected_bookmakers, nb_matchs
                            ) for comp in selected_competitions
                        ], ignore_index=True)

                    if not all_odds_df.empty:
                        all_odds_df["Payout"] = all_odds_df["Payout"].str.replace("%", "").str.replace(",", ".").astype(float)

                        trj_mean = all_odds_df.groupby("Bookmaker")["Payout"].mean().reset_index()
                        trj_mean.columns = ["Bookmaker", "Average Payout"]
                        trj_mean = trj_mean.sort_values(by="Average Payout", ascending=False)
                        trj_mean["Average Payout"] = trj_mean["Average Payout"].apply(lambda x: f"{x:.2f}%")

                        st.subheader("📊 Average Payout by Operator")
                        st.dataframe(trj_mean)

                        st.subheader("📌 Retrieved Odds")
                        st.dataframe(all_odds_df)

if __name__ == "__main__":
    main()
