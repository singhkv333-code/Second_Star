MOCK_PROFILE = {
    "user_id": "TEST001",
    "user_name": "Pivot Test User",
    "email": "test@pivot.com",
    "broker": "ZERODHA",
}

MOCK_HOLDINGS = [
    {"tradingsymbol": "INFY", "exchange": "NSE", "quantity": 10,
     "average_price": 1450.0, "last_price": 1523.0, "pnl": 730.0,
     "day_change": 12.5, "day_change_percentage": 0.83},
    {"tradingsymbol": "TCS", "exchange": "NSE", "quantity": 5,
     "average_price": 3200.0, "last_price": 3356.0, "pnl": 780.0,
     "day_change": -8.2, "day_change_percentage": -0.24},
    {"tradingsymbol": "HDFCBANK", "exchange": "NSE", "quantity": 20,
     "average_price": 1580.0, "last_price": 1643.0, "pnl": 1260.0,
     "day_change": 5.4, "day_change_percentage": 0.33},
    {"tradingsymbol": "NIFTYBEES", "exchange": "NSE", "quantity": 50,
     "average_price": 215.0, "last_price": 224.0, "pnl": 450.0,
     "day_change": 1.8, "day_change_percentage": 0.81},
    {"tradingsymbol": "GOLDBEES", "exchange": "NSE", "quantity": 30,
     "average_price": 58.0, "last_price": 62.5, "pnl": 135.0,
     "day_change": 0.4, "day_change_percentage": 0.64},
]

MOCK_POSITIONS = []

MOCK_MARGINS = {
    "equity": {"available": {"live_balance": 150000.0, "opening_balance": 150000.0},
               "utilised": {"debits": 0.0}},
    "commodity": {"available": {"live_balance": 0.0}},
}

MOCK_ORDERS = []

MOCK_QUOTE = {
    "NSE:NIFTY 50": {
        "last_price": 23456.0, "change": 120.5,
        "ohlc": {"open": 23340.0, "high": 23520.0, "low": 23290.0, "close": 23335.5}
    }
}
