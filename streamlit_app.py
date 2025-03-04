import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
import re


# 📌 Récupération des compétitions de football sans Selenium
def get_competitions():
    url = "https://www.coteur.com/cotes-foot"
    response = requests.get(url)
    if response.status_code != 200:
        st.error("⚠️ Impossible de récupérer les compétitions.")
        return pd.DataFrame()

    soup = BeautifulSoup(response.text, "html.parser")
    country_sections = soup.select("a.list-group-item.list-group-item-action.d-flex")

    competitions_list = []

    for section in country_sections:
        country_name = section.text.strip()
        href = section.get("href")
        if href:
            country_url = "https://www.coteur.com" + href
            country_response = requests.get(country_url)
            if country_response.status_code != 200:
                continue
            country_soup = BeautifulSoup(country_response.text, "html.parser")
            competition_links = country_soup.select("ul.list-group-flush a.list-group-item-action")

            for comp in competition_links:
                competition_name = comp.text.strip()
                competition_url = "https://www.coteur.com" + comp.get("href")
                competitions_list.append(
                    {"Pays": country_name, "Compétition": competition_name, "URL": competition_url})

    return pd.DataFrame(competitions_list)


# 📌 Scraper les cotes d'une compétition sans Selenium
def get_match_odds(competition_url, selected_bookmakers, nb_matchs):
    response = requests.get(competition_url)
    if response.status_code != 200:
        st.warning(f"⚠️ Impossible d'accéder à {competition_url}")
        return pd.DataFrame()

    soup = BeautifulSoup(response.text, "html.parser")
    match_links = [a.get("href") for a in soup.select("a.list-group-item.list-group-item-action")]
    match_links = ["https://www.coteur.com" + link.replace("/match/pronostic-", "/cote/") for link in match_links if
                   link][:nb_matchs]

    all_odds = []
    for match_url in match_links:
        response = requests.get(match_url)
        if response.status_code != 200:
            continue

        soup = BeautifulSoup(response.text, "html.parser")
        match_name = match_url.split("/")[-1].replace("-", " ").title()
        match_name = re.sub(r'\s*\d+#Cote\s*$', '', match_name).strip()

        odds_rows = soup.select("div.bookline")
        for row in odds_rows:
            bookmaker = row.get("data-name")
            odds_values = row.select("div.odds-col")
            payout_elem = row.select_one("div.border.bg-warning.payout")
            payout = payout_elem.text.strip() if payout_elem else "N/A"

            if len(odds_values) >= 3:
                odd_1, odd_n, odd_2 = [o.text.strip() for o in odds_values[:3]]
                if bookmaker in selected_bookmakers:
                    all_odds.append([match_name, bookmaker, odd_1, odd_n, odd_2, payout])

    return pd.DataFrame(all_odds, columns=["Match", "Bookmaker", "1", "Nul", "2", "Retour"])


# 📌 Interface principale Streamlit
def main():
    st.set_page_config(page_title="Scraping des Cotes", page_icon="⚽", layout="wide")
    st.sidebar.title("📌 Menu")

    if st.sidebar.button("📌 Récupérer les compétitions disponibles"):
        with st.spinner("Chargement des compétitions..."):
            competitions_df = get_competitions()
        st.session_state["competitions_df"] = competitions_df

    if "competitions_df" in st.session_state:
        competitions_df = st.session_state["competitions_df"]
        st.subheader("📌 Sélectionnez les compétitions à analyser")
        selected_competitions = st.multiselect("Choisissez les compétitions", competitions_df["Compétition"].tolist())

        if selected_competitions:
            all_bookmakers = ["Winamax", "Unibet", "Betclic", "Pmu", "ParionsSport", "Zebet", "Olybet", "Bwin", "Vbet",
                              "Genybet", "Feelingbet", "Betsson"]
            selected_bookmakers = st.multiselect("Sélectionnez les bookmakers", all_bookmakers, default=all_bookmakers)
            nb_matchs = st.slider("🔢 Nombre de matchs à récupérer par compétition", min_value=1, max_value=20, value=5)

            if st.button("🔍 Lancer le scraping des cotes"):
                with st.spinner("Scraping en cours..."):
                    all_odds_df = pd.concat([
                        get_match_odds(
                            competitions_df.loc[competitions_df["Compétition"] == comp, "URL"].values[0],
                            selected_bookmakers,
                            nb_matchs
                        ) for comp in selected_competitions
                    ])
                if not all_odds_df.empty:
                    all_odds_df["Retour"] = all_odds_df["Retour"].str.replace("%", "").astype(float)
                    trj_mean = all_odds_df.groupby("Bookmaker")["Retour"].mean().reset_index()
                    trj_mean.columns = ["Bookmaker", "Moyenne TRJ"]
                    trj_mean = trj_mean.sort_values(by="Moyenne TRJ", ascending=False)
                    trj_mean["Moyenne TRJ"] = trj_mean["Moyenne TRJ"].apply(lambda x: f"{x:.2f}%")
                    st.subheader("📊 Moyenne des TRJ par opérateur")
                    st.dataframe(trj_mean)
                st.subheader("📌 Cotes récupérées")
                st.dataframe(all_odds_df)


if __name__ == "__main__":
    main()
