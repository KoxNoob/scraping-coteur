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
import os


def init_driver():
    firefox_options = Options()
    firefox_options.add_argument("--headless")  # Mode headless obligatoire pour Streamlit Cloud
    firefox_options.add_argument("--no-sandbox")
    firefox_options.add_argument("--disable-dev-shm-usage")

    # ✅ NE PAS définir binary_location pour laisser Selenium détecter Firefox automatiquement
    # (C'est ainsi que cela fonctionnait avant)

    # ✅ Télécharger et utiliser Geckodriver automatiquement via WebDriver Manager
    service = Service(GeckoDriverManager().install())

    driver = webdriver.Firefox(service=service, options=firefox_options)
    return driver


# 📌 Récupération des compétitions de football (disponible uniquement en mode Admin)
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

    # Stocker les compétitions en mémoire
    st.session_state["competitions_df"] = competitions_df


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
        print(f"🔍 Scraping des cotes pour : {match_url}")
        driver.get(match_url)

        try:
            WebDriverWait(driver, 5).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.bookline"))
            )
        except:
            st.warning(f"⚠️ Aucune cote trouvée pour {match_url}")
            continue

        # 🔥 Vérifier que la page a bien changé en regardant le titre du match
        current_page_title = driver.title
        print(f"📄 Page actuelle : {current_page_title}")

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

        # 🛠 Solution 1 : Ajouter un `time.sleep(2)` pour s'assurer que la page est bien chargée
        time.sleep(2)

        # 🛠 Solution 2 : Rafraîchir la page pour éviter un problème de cache
        driver.refresh()
        time.sleep(2)

        # 🔥 Vérification des cotes extraites
        odds_list = driver.execute_script(odds_script)
        print(f"✅ Cotes extraites après rafraîchissement : {odds_list}")

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
    menu_selection = st.sidebar.radio("Choisissez un mode", ["🏠 Accueil", "⚽ Football", "🔑 Admin"])

    if menu_selection == "🔑 Admin":
        admin_password = st.sidebar.text_input("Mot de passe :", type="password")

        if admin_password == "monmotdepasse":
            st.sidebar.success("✅ Accès accordé")
            st.title("🔧 Mode Administrateur")

            if st.button("📌 Récupérer les compétitions disponibles"):
                with st.spinner("Chargement des compétitions..."):
                    get_competitions()
                st.success("✅ Compétitions mises à jour !")

            if "competitions_df" in st.session_state:
                st.dataframe(st.session_state["competitions_df"])
            else:
                st.warning("⚠️ Aucune donnée en mémoire. Veuillez récupérer les compétitions.")


    elif menu_selection == "⚽ Football":

        st.title("📊 Scraping des Cotes Football")

        # ⚠️ Vérifier que les compétitions sont bien en mémoire avant d'afficher les sélections
        if "competitions_df" not in st.session_state or st.session_state["competitions_df"].empty:
            st.warning(
                "⚠️ Aucune donnée en mémoire. Veuillez d'abord exécuter la récupération des compétitions en mode Admin.")

        else:
            competitions_df = st.session_state["competitions_df"]  # Utilisation directe du DataFrame stocké
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
                        # ✅ Convertir la colonne "Retour" en float pour le tri et l'affichage
                        all_odds_df["Retour"] = all_odds_df["Retour"].str.replace("%", "").str.replace(",", ".").astype(
                            float)

                        # ✅ Calculer la moyenne des TRJ par opérateur (trié en décroissant)
                        trj_mean = all_odds_df.groupby("Bookmaker")["Retour"].mean().reset_index()
                        trj_mean.columns = ["Bookmaker", "Moyenne TRJ"]
                        trj_mean = trj_mean.sort_values(by="Moyenne TRJ", ascending=False)
                        trj_mean["Moyenne TRJ"] = trj_mean["Moyenne TRJ"].apply(lambda x: f"{x:.2f}%")

                        # ✅ Réinitialiser l'index en partant de 1
                        trj_mean.reset_index(drop=True, inplace=True)
                        trj_mean.index = trj_mean.index + 1

                        # ✅ Trier les cotes par match en ordre décroissant de "Retour"
                        all_odds_df["Match_Order"] = all_odds_df.groupby(
                            "Match").ngroup()  # Ajoute un identifiant unique pour garder l'ordre original des matchs
                        all_odds_df = all_odds_df.sort_values(by=["Match_Order", "Retour"],
                                                              ascending=[False, False]).drop(columns=["Match_Order"])

                        # 🔹 Affichage des moyennes TRJ
                        st.subheader("📊 Moyenne des TRJ par opérateur")
                        st.dataframe(trj_mean)

                        # 🔹 Affichage des cotes triées par match
                        st.subheader("📌 Cotes récupérées")
                        st.dataframe(all_odds_df)

if __name__ == "__main__":
    main()
