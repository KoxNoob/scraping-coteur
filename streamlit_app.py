import streamlit as st
import pandas as pd
import json
import re
import time
from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.firefox import GeckoDriverManager

# Pour tester branche

# 📌 Chargement des compétitions depuis un fichier CSV
def load_competitions_from_csv(csv_path="URL Compétitions Football.csv"):
    try:
        competitions_df = pd.read_csv(csv_path)
        if "Pays" not in competitions_df.columns or "Compétition" not in competitions_df.columns or "URL" not in competitions_df.columns:
            st.error("⚠️ Le fichier CSV doit contenir les colonnes : 'Pays', 'Compétition', 'URL'.")
            return None
        st.session_state["competitions_df"] = competitions_df
        return competitions_df
    except Exception as e:
        st.error(f"⚠️ Erreur lors du chargement du fichier CSV : {e}")
        return None


# 📌 Fonction d'initialisation du WebDriver
def init_driver():
    firefox_options = Options()
    firefox_options.add_argument("--headless")
    firefox_options.add_argument("--no-sandbox")
    firefox_options.add_argument("--disable-dev-shm-usage")

    service = Service(GeckoDriverManager().install())
    driver = webdriver.Firefox(service=service, options=firefox_options)
    return driver


# 📌 Scraper les cotes d'une compétition (inchangé)
def get_match_odds(competition_url, selected_bookmakers, nb_matchs):
    driver = init_driver()
    driver.get(competition_url)

    try:
        WebDriverWait(driver, 10).until(
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
        print(f"🔍 Scraping des cotes pour : {match_url}")
        driver.delete_all_cookies()  # 🔥 Nettoyer le cache du navigateur avant de changer de match
        driver.get(match_url)

        # 🔄 Attendre que le titre change (évite de récupérer les cotes de la page précédente)
        previous_title = driver.title
        for attempt in range(3):
            time.sleep(3)
            if driver.title != previous_title:
                print(f"✅ Page bien mise à jour -> {driver.title}")
                break
            else:
                print("🔄 Tentative de rafraîchissement...")
                driver.refresh()

        try:
            # 🔥 Attendre que l'en-tête du match change (et pas juste les cotes)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1.match-title"))
            )
            time.sleep(3)  # Petit délai en plus pour s'assurer que tout est bien chargé
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

        max_retries = 3
        last_odds_list = []
        for attempt in range(max_retries):
            time.sleep(2)
            odds_list = driver.execute_script(odds_script)

            # 🔄 Vérifier si on obtient des cotes différentes de celles du match précédent
            if odds_list and odds_list != last_odds_list:
                print(f"✅ Cotes récupérées : {odds_list}")
                last_odds_list = odds_list  # Stocker les dernières cotes pour comparaison
                break
            elif attempt < max_retries - 1:
                print("⚠️ Aucune nouvelle cote détectée, tentative de rafraîchissement...")
                driver.refresh()
            else:
                print("❌ Échec de la récupération des cotes après plusieurs tentatives.")
                st.warning(f"⚠️ Impossible de récupérer les cotes pour {match_url}")

        match_name = driver.find_element(By.CSS_SELECTOR, "h1.match-title").text.strip()

        for odd in odds_list:
            if odd[0] in selected_bookmakers:
                all_odds.append([match_name] + odd)

    driver.quit()

    return pd.DataFrame(all_odds, columns=["Match", "Bookmaker", "1", "Nul", "2", "Retour"])





# 📌 Interface principale Streamlit
def main():
    st.set_page_config(page_title="Scraping des Cotes", page_icon="⚽", layout="wide")

    st.sidebar.title("📌 Menu")
    menu_selection = st.sidebar.radio("Choisissez un mode", ["🏠 Accueil", "⚽ Football", "🔑 Admin"])

    if menu_selection == "🔑 Admin":
        admin_password = st.sidebar.text_input("Mot de passe :", type="password")

        if admin_password == "gigtrading2025":
            st.sidebar.success("✅ Accès accordé")
            st.title("🔧 Mode Administrateur")
            st.warning("🛠️ Pour l'instant, la partie Admin n'a pas d'action spécifique.")

    elif menu_selection == "⚽ Football":
        st.title("📊 Scraping des Cotes Football")

        # 📌 Chargement des compétitions au démarrage
        if "competitions_df" not in st.session_state:
            load_competitions_from_csv()

        if "competitions_df" not in st.session_state or st.session_state["competitions_df"].empty:
            st.warning("⚠️ Aucune donnée en mémoire. Vérifiez le fichier CSV.")
        else:
            competitions_df = st.session_state["competitions_df"]
            selected_competitions = st.multiselect("📌 Sélectionnez les compétitions",
                                                   competitions_df["Compétition"].tolist())

            if selected_competitions:
                all_bookmakers = ["Winamax", "Unibet", "Betclic", "Pmu", "ParionsSport", "Zebet", "Olybet", "Bwin",
                                  "Vbet", "Genybet", "Feelingbet", "Betsson"]

                selected_bookmakers = st.multiselect("🎰 Sélectionnez les bookmakers", all_bookmakers,
                                                     default=all_bookmakers)
                nb_matchs = st.slider("🔢 Nombre de matchs par compétition", 1, 20, 5)

                if st.button("🔍 Lancer le scraping"):
                    with st.spinner("Scraping en cours..."):
                        all_odds_df = pd.concat([
                            get_match_odds(
                                competitions_df.loc[competitions_df["Compétition"] == comp, "URL"].values[0],
                                selected_bookmakers, nb_matchs
                            ) for comp in selected_competitions
                        ], ignore_index=True)

                    if not all_odds_df.empty:
                        all_odds_df["Retour"] = all_odds_df["Retour"].str.replace("%", "").str.replace(",", ".").astype(
                            float)

                        trj_mean = all_odds_df.groupby("Bookmaker")["Retour"].mean().reset_index()
                        trj_mean.columns = ["Bookmaker", "Moyenne TRJ"]
                        trj_mean = trj_mean.sort_values(by="Moyenne TRJ", ascending=False)
                        trj_mean["Moyenne TRJ"] = trj_mean["Moyenne TRJ"].apply(lambda x: f"{x:.2f}%")

                        trj_mean.index = trj_mean.index + 1

                        st.subheader("📊 Moyenne des TRJ par opérateur")
                        st.dataframe(trj_mean)

                        st.subheader("📌 Cotes récupérées")
                        st.dataframe(all_odds_df)


if __name__ == "__main__":
    main()
