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
    Initialize a Firefox WebDriver with GeckoDriverManager and anti-detection headers.
    """
    firefox_options = Options()
    if headless:
        firefox_options.add_argument("--headless")

    # Anti-detection: Simulate a real browser user agent
    firefox_options.set_preference("general.useragent.override",
                                   "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0")

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


def get_competitions_from_sheets(sheet_name: str,
                                 spreadsheet_id: str = "16ZBhF4k4ah-zhc3QcH7IEWLXrhbT8TRTMi5BptCFIcM") -> pd.DataFrame:
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



    driver = init_driver(headless=headless)
    driver.get(competition_url)

    # 1. Récupération des liens des matchs
    match_links = []
    try:
        # On attend que les lignes de match soient là
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CLASS_NAME, "match-row")))
        anchors = driver.find_elements(By.CSS_SELECTOR, "div.match-row a[href*='/cote/']")
        for a in anchors:
            match_links.append(a.get_attribute("href"))
    except Exception:
        st.warning(f"⚠️ Aucun match trouvé sur {competition_url}")
        driver.quit()
        return pd.DataFrame()

    match_links = list(dict.fromkeys(match_links))[:nb_matchs]



























    all_odds = []

    # Dictionnaire de correspondance ID -> Nom (à compléter si besoin)
    book_map = {
        "20": "Unibet", "21": "Pmu", "22": "ParionsSport",
        "24": "Betclic", "32": "Genybet", "33": "Winamax",
        "37": "Vbet", "43": "Betsson", "44": "Olybet"
    }

    for match_url in match_links:
        driver.get(match_url)
        time.sleep(2)  # Petit délai pour le rendu du tableau

        # Script JS adapté à la nouvelle structure tr/td



        odds_script = '''
        let results = [];
        // On cible toutes les lignes qui contiennent un lien vers un bookmaker
        document.querySelectorAll("tr").forEach(row => {
            let bookLink = row.querySelector("a[href*='/bookmaker/']");
            if (bookLink) {
                let href = bookLink.getAttribute("href");
                let bookId = href.split("/").pop(); // Récupère le '24'

                // On récupère toutes les colonnes text-center (les cotes)
                let cells = row.querySelectorAll("td.text-center");
                let cotes = Array.from(cells).map(c => c.innerText.trim());

                // On cherche le TRJ (souvent la dernière colonne ou une classe spécifique)
                let payout = row.querySelector(".payout, .text-bg-warning")?.innerText.trim() || "N/A";

                if (cotes.length >= 2) {
                    results.push({id: bookId, cotes: cotes, payout: payout});
                }
            }
        });
        return results;
        '''

        try:
            raw_data = driver.execute_script(odds_script)
        except:
            continue

        raw_name = match_url.split("/")[-1].replace("-", " ").title()
        # Supprime les chiffres (ID) à la fin du nom
        match_name = re.sub(r'\s*\d+$', '', raw_name).strip()

        for item in raw_data:
            b_name = book_map.get(item['id'], f"Bookmaker_{item['id']}")

            if b_name not in selected_bookmakers and item['id'] not in selected_bookmakers:
                continue

            # Conversion des cotes en nombres flottants
            try:
                c = [float(v.replace(',', '.')) for v in item['cotes'] if v]
                if not c: continue
            except ValueError:
                continue

            # CALCUL DU PAYOUT (TRJ)
            # Formule : 1 / ( (1/Cote1) + (1/Cote2) + ... ) * 100
            try:
                inv_sum = sum(1 / val for val in c[:outcomes_count])
                payout_val = (1 / inv_sum) * 100
            except ZeroDivisionError:
                payout_val = 0.0

            # Logique d'insertion
            if outcomes_count == 3 and len(c) >= 3:
                all_odds.append([match_name, b_name, c[0], c[1], c[2], payout_val])
            elif outcomes_count == 2 and len(c) >= 2:
                all_odds.append([match_name, b_name, c[0], c[-1], payout_val])

    driver.quit()

    cols = ["Match", "Bookmaker", "1", "Draw", "2", "Payout"] if outcomes_count == 3 else ["Match", "Bookmaker", "1",
                                                                                           "2", "Payout"]
    return pd.DataFrame(all_odds, columns=cols)



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
        [
            "🏠 Home", "⚽ Football", "🎾 Tennis", "🏉 Rugby",
            "🏀 Basket", "🤾 Handball",
            "🧊 Ice Hockey", "🥊 Boxing", "🏐 Volleyball", "🏈 American Football"
        ]
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

    elif menu_selection == "🧊 Ice Hockey":
        sport = "Ice Hockey"
        outcomes_count = 3
        run_sport_section(sport, outcomes_count)

    elif menu_selection == "🥊 Boxing":
        sport = "Boxing"
        outcomes_count = 3
        run_sport_section(sport, outcomes_count)

    elif menu_selection == "🏐 Volleyball":
        sport = "Volleyball"
        outcomes_count = 2
        run_sport_section(sport, outcomes_count)

    elif menu_selection == "🏈 American Football":
        sport = "American Football"
        outcomes_count = 2
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