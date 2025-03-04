import streamlit as st
import json
import re
import pandas as pd
import cloudscraper
from bs4 import BeautifulSoup

# 📌 Initialisation du scraper
scraper = cloudscraper.create_scraper()


# 📌 Récupération des compétitions avec Cloudscraper
def get_competitions():
    url = "https://www.coteur.com/cotes-foot"
    response = scraper.get(url).text
    soup = BeautifulSoup(response, "html.parser")

    competitions_list = []
    country_buttons = soup.select("a.list-group-item.list-group-item-action.d-flex")

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
                competitions_list.append(
                    {"Pays": country_name, "Compétition": competition_name, "URL": competition_url})

    return pd.DataFrame(competitions_list)


# 📌 Récupération des matchs d'une compétition et extraction des match_id
def get_match_ids(competition_url, nb_matchs):
    response = scraper.get(competition_url).text
    soup = BeautifulSoup(response, "html.parser")

    scripts = soup.find_all("script")
    match_ids = []

    for script in scripts:
        if '"@type":"SportsEvent"' in script.text:
            try:
                json_data = json.loads(re.sub(r'[\x00-\x1F\x7F]', '', script.text))
                if isinstance(json_data, dict) and "url" in json_data:
                    match_id = json_data["url"].split("-")[-1].replace("#cote", "")
                    match_ids.append(match_id)
            except json.JSONDecodeError:
                continue

    return match_ids[:nb_matchs]  # ✅ Limite au nombre de matchs sélectionné


# 📌 Scraper les cotes des matchs avec l'API de `coteur.com`
def get_match_odds(match_ids, selected_bookmakers):
    all_odds = []

    for match_id in match_ids:
        url = f"https://www.coteur.com/api/renc/avg/{match_id}"
        response = scraper.get(url).json()

        if "bookmakers" not in response:
            st.warning(f"⚠️ Aucune cote trouvée pour le match {match_id}")
            continue

        for bookmaker_data in response["bookmakers"]:
            bookmaker = bookmaker_data["name"]
            if bookmaker in selected_bookmakers:
                odd_1 = bookmaker_data.get("1", "N/A")
                odd_n = bookmaker_data.get("N", "N/A")
                odd_2 = bookmaker_data.get("2", "N/A")
                payout = bookmaker_data.get("payout", "N/A")

                all_odds.append([match_id, bookmaker, odd_1, odd_n, odd_2, payout])

    return pd.DataFrame(all_odds, columns=["Match ID", "Bookmaker", "1", "Nul", "2", "Retour"])


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
                        match_ids = []
                        for comp in selected_competitions:
                            comp_url = competitions_df.loc[competitions_df["Compétition"] == comp, "URL"].values[0]
                            match_ids += get_match_ids(comp_url, nb_matchs)

                        all_odds_df = get_match_odds(match_ids, selected_bookmakers)

                    if not all_odds_df.empty:
                        # ✅ Convertir la colonne "Retour" en float
                        all_odds_df["Retour"] = all_odds_df["Retour"].astype(str).str.replace("%", "").astype(float)

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
