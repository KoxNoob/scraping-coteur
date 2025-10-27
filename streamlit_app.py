# part1.py
# Core helpers for the Betting Odds Scraper (Football / Tennis / Rugby / Basket)
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


def get_trj_from_sheet(sheet_name: str, spreadsheet_id: str = "16ZBhF4k4ah-zhc3QcH7IEWLXrhbT8TRTMi5BptCFIcM") -> Dict[str, float]:
    """
    Robust function to load TRJ values from a specified sheet/tab.
    Attempts to find columns that represent bookmaker and TRJ/payout:
      - Bookmaker column candidates: 'Bookmaker', 'Operator', 'Opérateur', 'Parieur'
      - TRJ column candidates: 'TRJ', 'Payout', 'Payout %', 'Payout%', 'Payout (%)', 'Taux', 'Return'
    Returns a mapping {bookmaker_clean_name: trj_float}
    If nothing found, returns an empty dict.
    """
    client = _authorize_gsheets()
    try:
        sheet = client.open_by_key(spreadsheet_id).worksheet(sheet_name)
    except Exception as e:
        st.warning(f"Could not open TRJ sheet '{sheet_name}': {e}")
        return {}

    data = sheet.get_all_records()
    if not data:
        return {}

    df = pd.DataFrame(data)

    # Normalize column names for search
    cols_lower = {c.lower(): c for c in df.columns}

    bookmaker_candidates = ["bookmaker", "operator", "opérateur", "opérateur", "opérateur".lower(), "parieur"]
    trj_candidates = ["trj", "payout", "payout %", "payout%", "payout (%)", "taux", "return", "payout_percent", "payout_percent%"]

    found_bookmaker_col = None
    for cand in bookmaker_candidates:
        if cand in cols_lower:
            found_bookmaker_col = cols_lower[cand]
            break
    # fallback: try any column containing 'book' or 'oper'
    if not found_bookmaker_col:
        for k, orig in cols_lower.items():
            if "book" in k or "oper" in k:
                found_bookmaker_col = orig
                break

    found_trj_col = None
    for cand in trj_candidates:
        if cand in cols_lower:
            found_trj_col = cols_lower[cand]
            break
    # fallback: any numeric-like column that contains '%' in cell values
    if not found_trj_col:
        for col in df.columns:
            sample = df[col].astype(str).str.contains("%").any()
            if sample:
                found_trj_col = col
                break

    if not found_bookmaker_col or not found_trj_col:
        st.warning(f"TRJ table in sheet '{sheet_name}' does not contain recognizable bookmaker/TRJ columns.")
        return {}

    trj_map = {}
    for _, row in df.iterrows():
        bk = str(row.get(found_bookmaker_col, "")).strip()
        raw_trj = row.get(found_trj_col, "")
        if pd.isna(bk) or bk == "":
            continue
        # clean the TRJ cell
        if isinstance(raw_trj, str):
            cleaned = raw_trj.replace("%", "").replace(",", ".").strip()
        else:
            cleaned = raw_trj
        try:
            trj_val = float(cleaned)
        except Exception:
            # skip non-parsable
            continue
        trj_map[bk] = trj_val

    return trj_map


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
    - outcomes_count: 3 for (1 / Draw / 2) markets (football, rugby), 2 for (1 / 2) markets (tennis, basket).
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
                # clean invisible control chars then parse json
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

    match_links = list(dict.fromkeys(match_links))  # deduplicate while preserving order
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

        # small sleeps to make sure dynamic content is settled
        time.sleep(1.5)
        driver.refresh()
        time.sleep(1.5)

        # JS to extract odds lines. It returns arrays per bookmaker.
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

        # Build match name from URL if needed
        match_name = match_url.split("/")[-1].replace("-", " ").title()
        match_name = re.sub(r'\s*\d+#Cote\s*$', '', match_name).strip()

        for odd in odds_list:
            # odd[0] is bookmaker name per the script
            bookmaker_name = odd[0]
            if bookmaker_name not in selected_bookmakers:
                continue

            if len(odd) == 5:
                # format when JS pushed 3 odds: [bookmaker, odd_1, odd_n, odd_2, payout]
                if outcomes_count == 3:
                    row = [match_name, bookmaker_name, odd[1], odd[2], odd[3], odd[4]]
                else:
                    # we requested 2-way but got 3-way from page: try to map first and third as 1/2
                    row = [match_name, bookmaker_name, odd[1], odd[3], odd[4]]
            elif len(odd) == 4:
                # format when JS pushed 2 odds: [bookmaker, odd_1, odd_2, payout]
                if outcomes_count == 2:
                    row = [match_name, bookmaker_name, odd[1], odd[2], odd[3]]
                else:
                    # we requested 3-way but only 2 odds available: insert 'N/A' for draw
                    row = [match_name, bookmaker_name, odd[1], "N/A", odd[2], odd[3]]

            else:
                # unknown format; skip
                continue

            all_odds.append(row)

    driver.quit()

    # Build DataFrame with appropriate columns
    if outcomes_count == 3:
        column_names = ["Match", "Bookmaker", "1", "Draw", "2", "Payout"]
    else:
        column_names = ["Match", "Bookmaker", "1", "2", "Payout"]

    try:
        df = pd.DataFrame(all_odds, columns=column_names)
    except Exception:
        # fallback to a safe construction if data shape mismatches
        df = pd.DataFrame(all_odds)
        df.columns = df.columns.astype(str)

    return df


# --------------------------------------------
# TRJ display helper (shared)
# --------------------------------------------
def display_average_payouts(df: pd.DataFrame, sport: str):
    """
    Compute average 'Payout' per Bookmaker and display it in Streamlit.
    Expects a column 'Payout' with values like '93.5%' or '93,5%'.
    """
    if df is None or df.empty:
        st.info(f"No odds data available to compute TRJ for {sport}.")
        return

    # Normalize Payout column
    if "Payout" not in df.columns:
        st.warning("Payout column not found in odds DataFrame.")
        return

    # Clean and convert to float (percentage)
    df = df.copy()
    df["Payout"] = df["Payout"].astype(str).str.replace("%", "", regex=False).str.replace(",", ".", regex=False)
    # Remove empty or non-numeric rows
    df = df[df["Payout"].str.replace(".", "", 1).str.isnumeric() | df["Payout"].str.match(r"^\d+(\.\d+)?$")]
    try:
        df["Payout"] = df["Payout"].astype(float)
    except Exception:
        st.warning("Some Payout values could not be parsed to float. They will be ignored.")
        df["Payout"] = pd.to_numeric(df["Payout"], errors="coerce")

    trj_mean = df.groupby("Bookmaker")["Payout"].mean().reset_index()
    trj_mean.columns = ["Bookmaker", "Average Payout"]
    trj_mean = trj_mean.sort_values(by="Average Payout", ascending=False)
    trj_mean["Average Payout"] = trj_mean["Average Payout"].apply(lambda x: f"{x:.2f}%")
    st.subheader(f"📊 Average Payout by Operator - {sport}")
    st.dataframe(trj_mean)

# End of Part 1
# part2.py
# Streamlit UI integrating Football, Tennis, Rugby, and Basket
# Requires part1.py functions to be imported if split across files,
# or merged directly under the same script.


# 📌 MAIN STREAMLIT INTERFACE
def main():
    st.sidebar.title("📌 Menu")

    menu_selection = st.sidebar.radio(
        "Choose a mode",
        ["🏠 Home", "⚽ Football", "🎾 Tennis", "🏉 Rugby", "🏀 Basket"]
    )

    if menu_selection == "🏠 Home":
        st.title("Welcome to the Betting Odds Scraper 🏠")
        st.write("Use the sidebar to select a sport and start scraping odds from coteur.com.")

    # ---------------------- ⚽ FOOTBALL ----------------------
    elif menu_selection == "⚽ Football":
        sport = "Football"
        outcomes_count = 3  # 1 / N / 2
        run_sport_section(sport, outcomes_count)

    # ---------------------- 🎾 TENNIS ----------------------
    elif menu_selection == "🎾 Tennis":
        sport = "Tennis"
        outcomes_count = 2  # 1 / 2
        run_sport_section(sport, outcomes_count)

    # ---------------------- 🏉 RUGBY ----------------------
    elif menu_selection == "🏉 Rugby":
        sport = "Rugby"
        outcomes_count = 3  # works like Football (1 / Draw / 2)
        run_sport_section(sport, outcomes_count)

    # ---------------------- 🏀 BASKET ----------------------
    elif menu_selection == "🏀 Basket":
        sport = "Basket"
        outcomes_count = 2  # works like Tennis (1 / 2)
        run_sport_section(sport, outcomes_count)


# ------------------------------------------------
# ✅ Generic function for each sport section
# ------------------------------------------------
def run_sport_section(sport: str, outcomes_count: int):
    st.title(f"📊 {sport} Betting Odds Scraper")

    competitions_df = get_competitions_from_sheets(sport)

    if competitions_df.empty:
        st.warning(f"No competitions found for {sport}. Please check the Google Sheet tab '{sport}'.")
        return

    # Select competition(s)
    selected_competitions = st.multiselect("📌 Select competitions", competitions_df["Compétition"].tolist())

    if selected_competitions:
        # Bookmaker selection
        all_bookmakers = [
            "Winamax", "Unibet", "Betclic", "Pmu", "ParionsSport", "Zebet",
            "Olybet", "Bwin", "Vbet", "Genybet", "Feelingbet", "Betsson"
        ]
        selected_bookmakers = st.multiselect("🎰 Select bookmakers", all_bookmakers, default=all_bookmakers)

        # Matches to scrape per competition
        nb_matchs = st.slider("🔢 Number of matches per competition", 1, 20, 5)

        # Launch scraping
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


# ------------------------------------------------
# ✅ RUN THE APP
# ------------------------------------------------
if __name__ == "__main__":
    main()
