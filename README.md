# SEC Form 13F Investor Dashboard

Compares fund managers' quarterly 13F holdings (Q2 vs Q3 2026 by default) straight from SEC EDGAR: validated against each filing's Summary Page, amendment-aware, with a sourced five-bullet brief and Excel export.

Managers in the demo list: Berkshire Hathaway, Bridgewater, Coatue, Fortress, ExodusPoint, Pershing Square, Soros Fund Management, SkyBridge, Renaissance Technologies, PIMCO (via Allianz).

## Run locally
    pip install -r requirements.txt
    export SEC_USER_AGENT="Your Name you@example.com"
    streamlit run app.py

## Notes
- 13F shows quarter-end long positions only: no trade dates, shorts, bonds, or motives.
- Every result is a manager aggregate, not a single fund.
- Filings before about 2013 are text, not XML, and are not parsed.
