"""Built-in stock lists for the screener. Edit freely, or pass --universe-file with one symbol per line."""

# Nifty 50 large caps as listed on Yahoo (.NS). Index membership changes twice a year, so this is
# a close approximation rather than the official list; a couple of recent entrants are included.
NIFTY50 = [
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK", "BAJAJ-AUTO", "BAJFINANCE",
    "BAJAJFINSV", "BEL", "BHARTIARTL", "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
    "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO", "HINDALCO", "HINDUNILVR", "ICICIBANK",
    "INDIGO", "INDUSINDBK", "INFY", "ITC", "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M", "MARUTI",
    "MAXHEALTH", "NESTLEIND", "NTPC", "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN",
    "SUNPHARMA", "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN", "TMPV", "TRENT", "ULTRACEMCO", "WIPRO",
]

US_MEGACAP = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "BRK-B", "JPM", "V", "MA", "UNH",
    "XOM", "LLY", "JNJ", "PG", "HD", "COST", "ABBV", "MRK", "KO", "PEP", "BAC", "WMT", "NFLX", "AMD",
    "CRM", "ORCL", "ADBE",
]

UNIVERSES = {
    "nifty50": [s + ".NS" for s in NIFTY50],
    "us": US_MEGACAP,
}
