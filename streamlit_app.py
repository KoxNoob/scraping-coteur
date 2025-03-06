import streamlit as st
import json
import re
import pandas as pd
from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.firefox import GeckoDriverManager
from bs4 import BeautifulSoup
import time


# 📌 Configuration du navigateur Selenium pour Firefox
def init_driver():
    firefox_options = Options()
    firefox_options.add_argument("--headless")  # Mode sans interface graphique
    firefox_options.add_argument("--no-sandbox")
    firefox_options.add_argument("--disable-dev-shm-usage")

    service = Service(GeckoDriverManager().install())  # Utilisation de webdriver-manager
    driver = webdriver.Firefox(service=service, options=firefox_options)
    return driver


# 📌 Récupération des compétitions de football
def get_competitions():
    driver = init_driver()
    url = "https://www.coteur.com/cotes-foot"
    driver.get(url)

    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CLASS_NAME, "nav.flex-column.list-group.list-group-flush"))
    )

    country_buttons = driver.find_elements(By.CSS_SELECTOR, "a.list-group-item.list-group-item-action.d-flex")

    competitions_list = []

    for button in country_buttons:
        try:
            country_name = button.text.strip()
            driver.execute_script("arguments[0].click();", button)
            time.sleep(2)

            sub_menu_id = button.get_attribute("data-bs-target").replace("#", "")
            WebDriverWait(driver, 5).until(
                EC.visibility_of_element_located((By.ID, sub_menu_id))
            )

            soup = BeautifulSoup(driver.page_source, "html.parser")
            competition_menu = soup.find("ul", id=sub_menu_id)

            if competition_menu:
                for competition in competition_menu.find_all("a", class_="list-group-item-action"):
                    competition_name = competition.text.strip()
                    competition_url = "https://www.coteur.com" + competition["href"]
                    competitions_list.append(
                        {"Pays": country_name, "Compétition": f"{competition_name} ({country_name})",
                         "URL": competition_url}
                    )

        except Exception as e:
            print(f"⚠️ Erreur lors de l'ouverture de {country_name} : {e}")

    driver.quit()

    competitions_df = pd.DataFrame(competitions_list)
    competitions_df = competitions_df.sort_values(
        by=["Pays", "Compétition"],
        key=lambda x: x.map(lambda y: ("" if y == "France" else y))
    )

    return competitions_df


# 📌 Scraper les cotes d'une compétition
def get_match_odds(competition_url, selected_bookmakers, nb_matchs):
    driver = init_driver()
    driver.get(competition_url)

    try:
        WebDriverWait(driver, 5).until(
            EC.presence_of_all_elements_located((By.TAG_NAME, "script"))
        )
    except:
        st.warning(f"⚠️ Aucun match trouvé pour {competition_url}")
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
            st.warning(f"⚠️ Aucune cote trouvée pour {match_url}")
            continue

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

    return pd.DataFrame(all_odds, columns=["Match", "Bookmaker", "1", "Nul", "2", "Retour"])


# 📌 Interface principale Streamlit
def main():
    st.set_page_config(page_title="Scraping des Cotes", page_icon="⚽", layout="wide")

    st.sidebar.title("📌 Menu")
    selected_sport = st.sidebar.radio("Choisissez un sport", ["⚽ Football", "🏀⚾🎾 Autres Sports"])

    if selected_sport == "⚽ Football":
        st.title("📊 Scraping des Cotes Football")

        if st.button("📌 Récupérer les compétitions disponibles"):
            with st.spinner("Chargement des compétitions..."):
                competitions_df = get_competitions()
            st.session_state["competitions_df"] = competitions_df

        if "competitions_df" in st.session_state:
            competitions_df = st.session_state["competitions_df"]
            selected_competitions = st.multiselect("Choisissez les compétitions",
                                                   competitions_df["Compétition"].tolist())

            if selected_competitions:
                all_bookmakers = ["Winamax", "Unibet", "Betclic", "Pmu", "ParionsSport", "Zebet", "Olybet", "Bwin",
                                  "Vbet", "Genybet", "Feelingbet", "Betsson"]
                selected_bookmakers = st.multiselect("Sélectionnez les bookmakers", all_bookmakers,
                                                     default=all_bookmakers)

                nb_matchs = st.slider("🔢 Nombre de matchs", 1, 20, 5)

                if st.button("🔍 Lancer le scraping"):
                    all_odds_df = pd.concat([get_match_odds(
                        competitions_df.loc[competitions_df["Compétition"] == comp, "URL"].values[0],
                        selected_bookmakers, nb_matchs) for comp in selected_competitions])

                    if not all_odds_df.empty:
                        all_odds_df["Retour"] = all_odds_df["Retour"].str.replace("%", "").str.replace(",", ".").astype(
                            float)
                        trj_mean = all_odds_df.groupby("Bookmaker")["Retour"].mean().reset_index().sort_values(
                            by="Retour", ascending=False)
                        trj_mean["Retour"] = trj_mean["Retour"].apply(lambda x: f"{x:.2f}%")
                        st.subheader("📊 Moyenne des TRJ par opérateur")
                        st.dataframe(trj_mean)

                        st.subheader("📌 Cotes récupérées")
                        st.dataframe(all_odds_df)


if __name__ == "__main__":
    main()
