# part1.py
# Core helpers for the Betting Odds Scraper (Football / Tennis / Rugby / Basket / Handball)
# Author: Generated for Morgan — sports betting context, production-oriented.

import streamlit as st
import gspread
import pandas as pd
import json
import re
import time
from typing import Dict, List, Tuple
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.firefox import GeckoDriverManager

# ---------------------------
# Selenium driver initializer
# ---------------------------
def init_driver(headless: bool = True):
    """
    Initialize a Firefox WebDriver with GeckoDriverManager.
    headless=True is recommended for Streamlit Cloud / automated runs.
    """
    firefox_options = Options()
    if headless:
        firefox_options.add_argument("--headless")
    firefox_options.add_argument("--no-sandbox")
    firefox_options.add_argument("--disable-dev-shm-usage")

    service = Service(GeckoDriverManager().install())
    driver = webdriver.Firefox(service=service, options=firefox_options)
    return driver


# ---------------------------------------
# Google Sheets: competitions + TRJ fetch
# ---------------------------------------
def _authorize_gsheets():
    """
    Internal helper to create an authorized gspread client from Streamlit secrets.
    Expects st.secrets["GOOGLE_SHEET_CREDENTIALS"] to contain service account JSON.
    """
    credentials_dict = st.secrets.get("GOOGLE_SHEET_CREDENTIALS")
    if not credentials_dict:
        raise RuntimeError("Missing GOOGLE_SHEET_CREDENTIALS in st.secrets.")
    credentials = Credentials.from_service_account_info(
        credentials_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    client = gspread.authorize(credentials)
    return client


def get_competitions_from_sheets(sheet_name: str, spreadsheet_id: str = "16ZBhF4k4ah-zhc3QcH7IEWLXrhbT8TRTMi5BptCFIcM") -> pd.DataFrame:
    """
    Retrieve competitions for a given sheet/tab name.
    Expects the sheet tab to contain columns: 'Pays', 'Compétition', 'URL'
    Returns a DataFrame sorted with France first (if present).
    """
    client = _authorize_gsheets()
    try:
        sheet = client.open_by_key(spreadsheet_id).worksheet(sheet_name)
    except Exception as e:
        st.error(f"Unable to open worksheet '{sheet_name}': {e}")
        return pd.DataFrame()

    data = sheet.get_all_records()
    competitions_df = pd.DataFrame(data)

    required_columns = {"Pays", "Compétition", "URL"}
    if not required_columns.issubset(set(competitions_df.columns)):
        st.error(f"❌ The Google Sheet tab '{sheet_name}' must contain these columns: {required_columns}")
        return pd.DataFrame()

    # Sort with France first, then alphabetically
    competitions_df = competitions_df.sort_values(
        by=["Pays", "Compétition"],
        key=lambda x: x.map(lambda y: ("" if str(y).strip() == "France" else str(y)))
    ).reset_index(drop=True)

    return competitions_df


# --------------------------------------------
# Scraper: generic function for 2-way / 3-way
# --------------------------------------------
def get_match_odds(
    competition_url: str,
    selected_bookmakers: List[str],
    nb_matchs: int = 5,
    outcomes_count: int = 3,
    headless: bool = True
) -> pd.DataFrame:
    """
    Scrape matches from a competition page on coteur.com and retrieve odds for selected bookmakers.
    - outcomes_count: 3 for (1 / Draw / 2) markets (football, rugby, handball), 2 for (1 / 2) markets (tennis, basket).
    Returns a DataFrame with columns depending on outcomes_count:
      - 3-way: ["Match", "Bookmaker", "1", "Draw", "2", "Payout"]
      - 2-way: ["Match", "Bookmaker", "1", "2", "Payout"]
    """
    driver = init_driver(headless=headless)
    driver.get(competition_url)

    try:
        WebDriverWait(driver, 5).until(
            EC.presence_of_all_elements_located((By.TAG_NAME, "script"))
        )
    except Exception:
        st.warning(f"⚠️ No matches found for {competition_url}")
        driver.quit()
        return pd.DataFrame()

    scripts = driver.find_elements(By.TAG_NAME, "script")
    match_links = []

    # Extract JSON-LD scripts containing "@type":"SportsEvent"
    for script in scripts:
        inner = script.get_attribute("innerHTML")
        if '"@type":"SportsEvent"' in inner:
            try:
                cleaned = re.sub(r'[\x00-\x1F\x7F]', '', inner)
                json_data = json.loads(cleaned)
                if isinstance(json_data, dict) and "url" in json_data:
                    original_url = "https://www.coteur.com" + json_data["url"]
                    corrected_url = original_url.replace("/match/pronostic-", "/cote/")
                    match_links.append(corrected_url)
            except json.JSONDecodeError:
                continue

    # fallback: if no jsonld found, try to find match links by css (best-effort)
    if not match_links:
        try:
            anchors = driver.find_elements(By.CSS_SELECTOR, "a.btn.btn-primary")
            for a in anchors:
                href = a.get_attribute("href")
                if href and "/cote/" in href:
                    match_links.append(href)
        except Exception:
            pass

    match_links = list(dict.fromkeys(match_links))
    match_links = match_links[:nb_matchs]
    all_odds = []

    for match_url in match_links:
        driver.get(match_url)

        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.bookline"))
            )
        except Exception:
            st.warning(f"⚠️ No odds found for {match_url}")
            continue

        time.sleep(1.5)
        driver.refresh()
        time.sleep(1.5)

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
                let odd_n = odds.length >= 3 ? odds[2].innerText.trim() : "N/A";

                if (odds.length === 3) {
                    oddsData.push([bookmaker, odd_1, odd_n, odd_2, payout]);
                } else {
                    oddsData.push([bookmaker, odd_1, odd_2, payout]);
                }
            }
        });
        return oddsData;
        '''

        try:
            odds_list = driver.execute_script(odds_script)
        except Exception:
            odds_list = []

        match_name = match_url.split("/")[-1].replace("-", " ").title()
        match_name = re.sub(r'\s*\d+#Cote\s*$', '', match_name).strip()

        for odd in odds_list:
            bookmaker_name = odd[0]
            if bookmaker_name not in selected_bookmakers:
                continue

            if len(odd) == 5:
                if outcomes_count == 3:
                    row = [match_name, bookmaker_name, odd[1], odd[2], odd[3], odd[4]]
                else:
                    row = [match_name, bookmaker_name, odd[1], odd[3], odd[4]]
            elif len(odd) == 4:
                if outcomes_count == 2:
                    row = [match_name, bookmaker_name, odd[1], odd[2], odd[3]]
                else:
                    row = [match_name, bookmaker_name, odd[1], "N/A", odd[2], odd[3]]
            else:
                continue

            all_odds.append(row)

    driver.quit()

    column_names = ["Match", "Bookmaker", "1", "Draw", "2", "Payout"] if outcomes_count == 3 else ["Match", "Bookmaker", "1", "2", "Payout"]
    df = pd.DataFrame(all_odds, columns=column_names)
    return df


# --------------------------------------------
# TRJ display helper (shared)
# --------------------------------------------
def display_average_payouts(df: pd.DataFrame, sport: str):
    if df is None or df.empty:
        st.info(f"No odds data available to compute TRJ for {sport}.")
        return

    if "Payout" not in df.columns:
        st.warning("Payout column not found in odds DataFrame.")
        return

    df = df.copy()
    df["Payout"] = df["Payout"].astype(str).str.replace("%", "", regex=False).str.replace(",", ".", regex=False)
    df = df[df["Payout"].str.replace(".", "", 1).str.isnumeric() | df["Payout"].str.match(r"^\d+(\.\d+)?$")]
    df["Payout"] = pd.to_numeric(df["Payout"], errors="coerce")

    trj_mean = df.groupby("Bookmaker")["Payout"].mean().reset_index()
    trj_mean.columns = ["Bookmaker", "Average Payout"]
    trj_mean = trj_mean.sort_values(by="Average Payout", ascending=False)
    trj_mean["Average Payout"] = trj_mean["Average Payout"].apply(lambda x: f"{x:.2f}%")
    st.subheader(f"📊 Average Payout by Operator - {sport}")
    st.dataframe(trj_mean)


# part2.py
# Streamlit UI integrating Football, Tennis, Rugby, Basket and Handball
def main():
    st.sidebar.title("📌 Menu")

    menu_selection = st.sidebar.radio(
        "Choose a mode",
        ["🏠 Home", "⚽ Football", "🎾 Tennis", "🏉 Rugby", "🏀 Basket", "🤾 Handball"]
    )

    if menu_selection == "🏠 Home":
        st.title("Welcome to the Betting Odds Scraper 🏠")
        st.write("Use the sidebar to select a sport and start scraping odds from coteur.com.")

    elif menu_selection == "⚽ Football":
        sport = "Football"
        outcomes_count = 3
        run_sport_section(sport, outcomes_count)

    elif menu_selection == "🎾 Tennis":
        sport = "Tennis"
        outcomes_count = 2
        run_sport_section(sport, outcomes_count)

    elif menu_selection == "🏉 Rugby":
        sport = "Rugby"
        outcomes_count = 3
        run_sport_section(sport, outcomes_count)

    elif menu_selection == "🏀 Basket":
        sport = "Basket"
        outcomes_count = 2
        run_sport_section(sport, outcomes_count)

    elif menu_selection == "🤾 Handball":
        sport = "Handball"
        outcomes_count = 3
        run_sport_section(sport, outcomes_count)


def run_sport_section(sport: str, outcomes_count: int):
    st.title(f"📊 {sport} Betting Odds Scraper")

    competitions_df = get_competitions_from_sheets(sport)

    if competitions_df.empty:
        st.warning(f"No competitions found for {sport}. Please check the Google Sheet tab '{sport}'.")
        return

    selected_competitions = st.multiselect("📌 Select competitions", competitions_df["Compétition"].tolist())

    if selected_competitions:
        all_bookmakers = [
            "Winamax", "Unibet", "Betclic", "Pmu", "ParionsSport", "Zebet",
            "Olybet", "Bwin", "Vbet", "Genybet", "Feelingbet", "Betsson"
        ]
        selected_bookmakers = st.multiselect("🎰 Select bookmakers", all_bookmakers, default=all_bookmakers)

        nb_matchs = st.slider("🔢 Number of matches per competition", 1, 20, 5)

        if st.button("🔍 Start scraping"):
            with st.spinner("Scraping in progress..."):
                all_odds_df = pd.DataFrame()

                for comp in selected_competitions:
                    comp_url = competitions_df.loc[
                        competitions_df["Compétition"] == comp, "URL"
                    ].values[0]

                    scraped_df = get_match_odds(
                        comp_url,
                        selected_bookmakers,
                        nb_matchs=nb_matchs,
                        outcomes_count=outcomes_count
                    )
                    all_odds_df = pd.concat([all_odds_df, scraped_df], ignore_index=True)

                if not all_odds_df.empty:
                    display_average_payouts(all_odds_df, sport)
                    st.subheader(f"📌 Retrieved {sport} Odds")
                    st.dataframe(all_odds_df)
                else:
                    st.info(f"No odds retrieved for {sport}.")
    else:
        st.info("Please select at least one competition to begin.")


if __name__ == "__main__":
    main()
