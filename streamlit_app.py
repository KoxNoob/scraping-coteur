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
    return webdriver.Firefox(service=service, options=firefox_options)


# ---------------------------------------
# Google Sheets: competitions + TRJ fetch
# ---------------------------------------
def _authorize_gsheets():
    credentials_dict = st.secrets.get("GOOGLE_SHEET_CREDENTIALS")
    if not credentials_dict:
        raise RuntimeError("Missing GOOGLE_SHEET_CREDENTIALS in st.secrets.")
    credentials = Credentials.from_service_account_info(credentials_dict,
                                                        scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(credentials)


def get_competitions_from_sheets(sheet_name: str,
                                 spreadsheet_id: str = "16ZBhF4k4ah-zhc3QcH7IEWLXrhbT8TRTMi5BptCFIcM") -> pd.DataFrame:
    client = _authorize_gsheets()
    try:
        sheet = client.open_by_key(spreadsheet_id).worksheet(sheet_name)
        data = sheet.get_all_records()
        df = pd.DataFrame(data)
        df = df.sort_values(by=["Pays", "Compétition"],
                            key=lambda x: x.map(lambda y: ("" if str(y).strip() == "France" else str(y)))).reset_index(
            drop=True)
        return df
    except Exception as e:
        st.error(f"Erreur GSheets: {e}")
        return pd.DataFrame()


# --------------------------------------------
# Scraper: La fonction qui doit marcher
# --------------------------------------------
def get_match_odds(competition_url: str, selected_bookmakers: List[str], nb_matchs: int = 5, outcomes_count: int = 3,
                   headless: bool = True) -> pd.DataFrame:
    driver = init_driver(headless=headless)
    driver.get(competition_url)

    match_links = []
    try:
        # On attend les liens de match (plus robuste)
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/cote/']")))
        anchors = driver.find_elements(By.CSS_SELECTOR, "a[href*='/cote/']")
        for a in anchors:
            href = a.get_attribute("href")
            if href and "/match/" not in href:  # On veut les pages de cotes, pas de prono
                match_links.append(href)
    except:
        driver.quit()
        return pd.DataFrame()

    match_links = list(dict.fromkeys(match_links))[:nb_matchs]
    all_odds = []

    # Ton mapping d'IDs
    book_map = {"20": "Unibet", "21": "Pmu", "22": "ParionsSport", "24": "Betclic", "32": "Genybet", "33": "Winamax",
                "37": "Vbet", "43": "Betsson", "44": "Olybet"}

    for match_url in match_links:
        driver.get(match_url)
        try:
            # ATTENTE CRUCIALE : On attend que le tableau de cotes soit chargé
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "tr td.text-center")))
        except:
            continue

        odds_script = '''
        let results = [];
        document.querySelectorAll("tr").forEach(row => {
            let bookLink = row.querySelector("a[href*='/bookmaker/']");
            let cells = row.querySelectorAll("td.text-center");
            if (bookLink && cells.length >= 2) {
                let bookId = bookLink.getAttribute("href").split("/").pop();
                let cotes = Array.from(cells).map(c => c.innerText.trim());
                results.push({id: bookId, cotes: cotes});
            }
        });
        return results;
        '''

        raw_data = driver.execute_script(odds_script)
        match_name = re.sub(r'\s*\d+$', '', match_url.split("/")[-1].replace("-", " ").title()).strip()

        for item in raw_data:
            b_name = book_map.get(item['id'], f"ID_{item['id']}")

            # Si on a un ID inconnu mais qu'il est dans la liste de sélection, on le garde
            if b_name not in selected_bookmakers and item['id'] not in selected_bookmakers:
                continue

            try:
                c = [float(v.replace(',', '.')) for v in item['cotes'] if v]
                if not c: continue

                # Calcul TRJ
                if outcomes_count == 3 and len(c) >= 3:
                    trj = (1 / ((1 / c[0]) + (1 / c[1]) + (1 / c[2]))) * 100
                    all_odds.append([match_name, b_name, c[0], c[1], c[2], trj])
                elif outcomes_count == 2 and len(c) >= 2:
                    trj = (1 / ((1 / c[0]) + (1 / c[-1]))) * 100
                    all_odds.append([match_name, b_name, c[0], c[-1], trj])
            except:
                continue

    driver.quit()
    cols = ["Match", "Bookmaker", "1", "Draw", "2", "Payout"] if outcomes_count == 3 else ["Match", "Bookmaker", "1",
                                                                                           "2", "Payout"]
    return pd.DataFrame(all_odds, columns=cols)


# --------------------------------------------
# TRJ & UI Sections (Restant du code d'origine)
# --------------------------------------------
def display_average_payouts(df: pd.DataFrame, sport: str):
    if df.empty: return
    st.subheader(f"📊 Average Payout - {sport}")
    df["Payout"] = pd.to_numeric(df["Payout"], errors="coerce")
    res = df.groupby("Bookmaker")["Payout"].mean().reset_index().sort_values(by="Payout", ascending=False)
    res["Payout"] = res["Payout"].apply(lambda x: f"{x:.2f}%")
    st.dataframe(res, use_container_width=True)


def run_sport_section(sport: str, outcomes_count: int):
    st.title(f"📊 {sport} Scraper")
    comp_df = get_competitions_from_sheets(sport)
    if comp_df.empty: return

    selected = st.multiselect("📌 Competitions", comp_df["Compétition"].tolist())
    if selected:
        all_books = ["Winamax", "Unibet", "Betclic", "Pmu", "ParionsSport", "Zebet", "Olybet", "Bwin", "Vbet",
                     "Genybet", "Betsson"]
        sel_books = st.multiselect("🎰 Bookmakers", all_books, default=all_books)
        nb = st.slider("Matchs", 1, 15, 5)

        if st.button("🔍 Scrap"):
            with st.spinner("Scraping..."):
                final = pd.DataFrame()
                for c in selected:
                    url = comp_df.loc[comp_df["Compétition"] == c, "URL"].values[0]
                    df = get_match_odds(url, sel_books, nb, outcomes_count)
                    final = pd.concat([final, df], ignore_index=True)

                if not final.empty:
                    display_average_payouts(final, sport)
                    disp = final.copy()
                    disp["Payout"] = disp["Payout"].apply(lambda x: f"{x:.2f}%")
                    st.dataframe(disp, use_container_width=True)
                else:
                    st.error(f"No odds retrieved for {sport}.")


def main():
    st.sidebar.title("📌 Menu")
    m = st.sidebar.radio("Sport",
                         ["🏠 Home", "⚽ Football", "🎾 Tennis", "🏉 Rugby", "🏀 Basket", "🤾 Handball", "🧊 Ice Hockey",
                          "🥊 Boxing"])
    if m == "🏠 Home":
        st.title("Betting Scraper 🏠")
    else:
        s = m.split(" ")[1]
        out = 2 if s in ["Tennis", "Basket"] else 3
        run_sport_section(s, out)


if __name__ == "__main__":
    main()