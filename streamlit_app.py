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
    firefox_options = Options()
    if headless:
        firefox_options.add_argument("--headless")

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

    competitions_df = competitions_df.sort_values(
        by=["Pays", "Compétition"],
        key=lambda x: x.map(lambda y: ("" if str(y).strip() == "France" else str(y)))
    ).reset_index(drop=True)

    return competitions_df


# --------------------------------------------
# Scraper: logic for 2-way / 3-way
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

    match_links = []
    try:
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CLASS_NAME, "match-row")))
        anchors = driver.find_elements(By.CSS_SELECTOR, "div.match-row a[href*='/cote/']")
        for a in anchors:
            match_links.append(a.get_attribute("href"))
    except Exception:
        driver.quit()
        return pd.DataFrame()

    match_links = list(dict.fromkeys(match_links))[:nb_matchs]
    all_odds = []

    # Mapping complet basé sur tes IDs
    book_map = {
        "20": "Unibet", "21": "Pmu", "22": "ParionsSport",
        "24": "Betclic", "32": "Genybet", "33": "Winamax",
        "37": "Vbet", "43": "Betsson", "44": "Olybet"
    }

    for match_url in match_links:
        driver.get(match_url)
        time.sleep(2)

        odds_script = '''
        let results = [];
        document.querySelectorAll("tr").forEach(row => {
            let bookLink = row.querySelector("a[href*='/bookmaker/']");
            if (bookLink) {
                let bookId = bookLink.getAttribute("href").split("/").pop();
                let cells = row.querySelectorAll("td.text-center");
                let cotes = Array.from(cells).map(c => c.innerText.trim());
                if (cotes.length >= 2) {
                    results.push({id: bookId, cotes: cotes});
                }
            }
        });
        return results;
        '''

        try:
            raw_data = driver.execute_script(odds_script)
        except:
            continue

        # Nettoyage propre du nom (suppression ID numérique)
        match_name = match_url.split("/")[-1].replace("-", " ").title()
        match_name = re.sub(r'\s*\d+$', '', match_name).strip()

        for item in raw_data:
            b_name = book_map.get(item['id'], f"ID_{item['id']}")
            if b_name not in selected_bookmakers:
                continue

            try:
                c = [float(val.replace(',', '.')) for val in item['cotes']]
                payout = 0.0

                # Calcul TRJ dynamique selon le sport
                if outcomes_count == 3 and len(c) >= 3:
                    payout = (1 / ((1 / c[0]) + (1 / c[1]) + (1 / c[2]))) * 100
                    all_odds.append([match_name, b_name, c[0], c[1], c[2], payout])
                elif outcomes_count == 2 and len(c) >= 2:
                    # On prend la première et la dernière (évite le nul si présent)
                    payout = (1 / ((1 / c[0]) + (1 / c[-1]))) * 100
                    all_odds.append([match_name, b_name, c[0], c[-1], payout])
            except:
                continue

    driver.quit()

    cols = ["Match", "Bookmaker", "1", "Draw", "2", "Payout"] if outcomes_count == 3 else ["Match", "Bookmaker", "1",
                                                                                           "2", "Payout"]
    return pd.DataFrame(all_odds, columns=cols)


# --------------------------------------------
# TRJ display helper
# --------------------------------------------
def display_average_payouts(df: pd.DataFrame, sport: str):
    if df.empty:
        return
    st.subheader(f"📊 Average Payout by Operator - {sport}")
    df["Payout"] = pd.to_numeric(df["Payout"], errors="coerce")
    trj_mean = df.groupby("Bookmaker")["Payout"].mean().reset_index()
    trj_mean = trj_mean.sort_values(by="Payout", ascending=False)
    trj_mean["Payout"] = trj_mean["Payout"].apply(lambda x: f"{x:.2f}%")
    st.table(trj_mean)


# --------------------------------------------
# UI Core Section
# --------------------------------------------
def run_sport_section(sport: str, outcomes_count: int):
    st.title(f"📊 {sport} Betting Odds Scraper")
    competitions_df = get_competitions_from_sheets(sport)

    if competitions_df.empty:
        st.warning(f"No competitions found for {sport}. Check Google Sheet tab '{sport}'.")
        return

    selected_competitions = st.multiselect("📌 Select competitions", competitions_df["Compétition"].tolist())

    if selected_competitions:
        all_bookmakers = ["Winamax", "Unibet", "Betclic", "Pmu", "ParionsSport", "Zebet", "Olybet", "Bwin", "Vbet",
                          "Genybet", "Betsson"]
        selected_bookmakers = st.multiselect("🎰 Select bookmakers", all_bookmakers, default=all_bookmakers)
        nb_matchs = st.slider("🔢 Number of matches per competition", 1, 20, 5)

        if st.button("🔍 Start scraping"):
            with st.spinner("Scraping in progress..."):
                all_odds_df = pd.DataFrame()
                for comp in selected_competitions:
                    comp_url = competitions_df.loc[competitions_df["Compétition"] == comp, "URL"].values[0]
                    scraped_df = get_match_odds(comp_url, selected_bookmakers, nb_matchs, outcomes_count)
                    all_odds_df = pd.concat([all_odds_df, scraped_df], ignore_index=True)

                if not all_odds_df.empty:
                    display_average_payouts(all_odds_df, sport)
                    st.subheader(f"📌 Retrieved {sport} Odds")
                    styled_df = all_odds_df.copy()
                    styled_df["Payout"] = styled_df["Payout"].apply(lambda x: f"{x:.2f}%")
                    st.dataframe(styled_df, use_container_width=True)
                else:
                    st.info(f"No odds retrieved for {sport}.")


# --------------------------------------------
# Main App
# --------------------------------------------
def main():
    st.sidebar.title("📌 Menu")
    menu_selection = st.sidebar.radio(
        "Choose a mode",
        ["🏠 Home", "⚽ Football", "🎾 Tennis", "🏉 Rugby", "🏀 Basket", "🤾 Handball",
         "🧊 Ice Hockey", "🥊 Boxing", "🏐 Volleyball", "🏈 American Football"]
    )

    if menu_selection == "🏠 Home":
        st.title("Welcome to the Betting Odds Scraper 🏠")
        st.write("Use the sidebar to select a sport.")
    else:
        # Extraction du nom propre pour GSheets et définition des issues
        sport = menu_selection.split(" ")[1] if " " in menu_selection else menu_selection
        # Logique de décompte des issues
        outcomes = 2 if sport in ["Tennis", "Basket", "Volleyball", "American Football"] else 3
        run_sport_section(sport, outcomes)


if __name__ == "__main__":
    main()