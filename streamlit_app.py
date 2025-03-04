import streamlit as st
import json
import re
import pandas as pd
import cloudscraper
from bs4 import BeautifulSoup


# 📌 Récupération des compétitions avec Cloudscraper
def get_competitions():
    scraper = cloudscraper.create_scraper()
    url = "https://www.coteur.com/cotes-foot"
    response = scraper.get(url).text

    soup = BeautifulSoup(response, "html.parser")
    country_buttons = soup.select("a.list-group-item.list-group-item-action.d-flex")

    competitions_list = []

    for button in country_buttons:
        country_name = button.text.strip()
        sub_menu_id = button.get("data-bs-target")

        if not sub_menu_id:
            continue

        sub_menu_id = sub_menu_id.replace("#", "")
        sub_menu = soup.find("ul", id=sub_menu_id)

        if sub_menu:
            for competition in sub_menu.find_all("a", class_="list-group-item-action"):
                competition_name = competition.text.strip()
                competition_url = "https://www.coteur.com" + competition["href"]
                competitions_list.append({"Pays": country_name, "Compétition": competition_name, "URL": competition_url})

    return pd.DataFrame(competitions_list)


# 📌 Scraper les cotes des matchs avec Cloudscraper
def get_match_odds(competition_url, selected_bookmakers, nb_matchs):
    scraper = cloudscraper.create_scraper()
    response = scraper.get(competition_url).text

    soup = BeautifulSoup(response, "html.parser")
    scripts = soup.find_all("script")

    match_links = []
    for script in scripts:
        if '"@type":"SportsEvent"' in script.text:
            try:
                json_data = json.loads(re.sub(r'[\x00-\x1F\x7F]', '', script.text))
                if isinstance(json_data, dict) and "url" in json_data:
                    original_url = "https://www.coteur.com" + json_data["url"]
                    corrected_url = original_url.replace("/match/pronostic-", "/cote/")
                    match_links.append(corrected_url)
            except json.JSONDecodeError:
                continue

    match_links = match_links[:nb_matchs]  # ✅ Limite le nombre de matchs

    all_odds = []
    for match_url in match_links:
        response = scraper.get(match_url).text
        soup = BeautifulSoup(response, "html.parser")

        booklines = soup.select("div.bookline")
        if not booklines:
            st.warning(f"⚠️ Aucune cote trouvée pour {match_url}")  # ✅ Alerte si aucun bookmaker n'est trouvé
            continue

        for row in booklines:
            bookmaker = row.get("data-name", "Inconnu")  # ✅ Ajout d'une vérification
            odds = row.select("div.odds-col")
            payout_elem = row.select_one("div.border.bg-warning.payout")
            payout = payout_elem.text.strip() if payout_elem else "N/A"

            if len(odds) >= 3:
                odd_1 = odds[0].text.strip()
                odd_n = odds[1].text.strip()
                odd_2 = odds[2].text.strip()
                match_name = match_url.split("/")[-1].replace("-", " ").title()
                match_name = re.sub(r'\s*\d+#Cote\s*$', '', match_name).strip()  # ✅ Nettoyage du nom du match

                if bookmaker in selected_bookmakers:
                    all_odds.append([match_name, bookmaker, odd_1, odd_n, odd_2, payout])

    return pd.DataFrame(all_odds, columns=["Match", "Bookmaker", "1", "Nul", "2", "Retour"])


# 📌 Interface principale Streamlit
def main():
    st.set_page_config(page_title="Scraping des Cotes", page_icon="⚽", layout="wide")

    # 📌 Menu latéral
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
            st.subheader("📌 Sélectionnez les compétitions à analyser")
            selected_competitions = st.multiselect(
                "Choisissez les compétitions",
                competitions_df["Compétition"].tolist()
            )

            if selected_competitions:
                all_bookmakers = ["Winamax", "Unibet", "Betclic", "Pmu", "ParionsSport", "Zebet", "Olybet", "Bwin",
                                  "Vbet", "Genybet", "Feelingbet", "Betsson"]
                selected_bookmakers = st.multiselect("Sélectionnez les bookmakers", all_bookmakers,
                                                     default=all_bookmakers)

                nb_matchs = st.slider("🔢 Nombre de matchs à récupérer par compétition", min_value=1, max_value=20,
                                      value=5)

                if st.button("🔍 Lancer le scraping des cotes"):
                    with st.spinner("Scraping en cours..."):
                        all_odds_df = pd.concat([
                            get_match_odds(
                                competitions_df.loc[competitions_df["Compétition"] == comp, "URL"].values[0],
                                selected_bookmakers,
                                nb_matchs
                            )
                            for comp in selected_competitions
                        ])

                    if not all_odds_df.empty:
                        # ✅ Convertir la colonne "Retour" en float
                        all_odds_df["Retour"] = all_odds_df["Retour"].str.replace("%", "").astype(float)

                        # 🔹 Moyennes TRJ par opérateur
                        trj_mean = all_odds_df.groupby("Bookmaker")["Retour"].mean().reset_index()
                        trj_mean.columns = ["Bookmaker", "Moyenne TRJ"]

                        # Trier les TRJ en ordre décroissant + décimale = 2
                        trj_mean = trj_mean.sort_values(by="Moyenne TRJ", ascending=False)
                        trj_mean["Moyenne TRJ"] = trj_mean["Moyenne TRJ"].apply(lambda x: f"{x:.2f}%")

                        # 🔹 Affichage des moyennes TRJ
                        st.subheader("📊 Moyenne des TRJ par opérateur")
                        st.dataframe(trj_mean)

                    st.subheader("📌 Cotes récupérées")
                    st.dataframe(all_odds_df)

    else:
        st.title("🏀⚾🎾 Autres Sports")
        st.image("https://upload.wikimedia.org/wikipedia/commons/3/3a/Under_construction_icon-yellow.svg",
                 caption="🚧 En cours de développement...", use_column_width=True)


# Exécution de l'application Streamlit
if __name__ == "__main__":
    main()
