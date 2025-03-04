import streamlit as st
import requests
import json
import pandas as pd

# 📌 API Endpoints
BASE_URL = "https://www.coteur.com/api"
BOOKMAKERS_URL = f"{BASE_URL}/bookmakers/all"
MATCH_ODDS_URL = f"{BASE_URL}/renc/avg/"


# 📌 Récupération des bookmakers
def get_bookmakers():
    response = requests.get(BOOKMAKERS_URL)
    if response.status_code == 200:
        return {str(b['id']): b['nom'] for b in response.json()}
    return {}


# 📌 Récupération des cotes pour un match
def get_match_odds(match_id, bookmakers_dict):
    response = requests.get(f"{MATCH_ODDS_URL}{match_id}")
    if response.status_code == 200:
        odds_data = response.json()
        all_odds = []
        for odd in odds_data:
            bookmaker_name = bookmakers_dict.get(str(odd.get('id', '')), 'Inconnu')
            all_odds.append([
                match_id, bookmaker_name, odd['cote_dom'], odd['cote_nul'], odd['cote_ext'], odd['published']
            ])
        return pd.DataFrame(all_odds, columns=["Match ID", "Bookmaker", "1", "Nul", "2", "Date de Publication"])
    return pd.DataFrame()


# 📌 Interface principale Streamlit
def main():
    st.set_page_config(page_title="Scraping des Cotes", page_icon="⚽", layout="wide")

    st.title("📊 Scraping des Cotes Football")

    # 📌 Récupérer la liste des bookmakers
    st.sidebar.header("🔹 Sélection des Bookmakers")
    bookmakers_dict = get_bookmakers()
    all_bookmakers = list(bookmakers_dict.values())
    selected_bookmakers = st.sidebar.multiselect("Sélectionnez les bookmakers", all_bookmakers, default=all_bookmakers)

    # 📌 Entrée de l'ID du match
    match_id = st.text_input("Entrez l'ID du match (ex: 1515949)")

    if st.button("🔍 Lancer le scraping des cotes"):
        with st.spinner("Scraping en cours..."):
            odds_df = get_match_odds(match_id, bookmakers_dict)
            if not odds_df.empty:
                # Filtrer uniquement les bookmakers sélectionnés
                odds_df = odds_df[odds_df["Bookmaker"].isin(selected_bookmakers)]
                st.subheader("📌 Cotes récupérées")
                st.dataframe(odds_df)
            else:
                st.warning("⚠️ Aucune cote trouvée pour ce match.")


if __name__ == "__main__":
    main()
