# LOC Recommendation

Streamlit app that recommends warehouse storage locations (LOCs) for inbound SKUs based on historical data, velocity, and configurable rules.

## Features

- Query inbound / inventory data from MySQL
- Score and rank candidate LOCs per SKU
- Configurable rules: velocity tier, zone affinity, weight/volume constraints
- Interactive simulator: adjust rule weights and see re-ranked results
- Export recommendations to CSV or Google Sheet

## Setup

```bash
cd loc-recommendation
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # fill in credentials
streamlit run app.py
```

## Project Structure

```
loc-recommendation/
├── app.py              # Streamlit entry point
├── config.py           # Rules, weights, constants
├── data_loader.py      # MySQL queries
├── scoring.py          # LOC scoring logic
├── validators.py       # Input validation
├── exporter.py         # CSV / Google Sheet export
├── requirements.txt
├── .env.example
├── .streamlit/
│   └── config.toml
└── tests/
    └── test_scoring.py
```
