from binance import ThreadedWebsocketManager
import pandas as pd

from app.strategies.market_structure import (
    detect_swings,
    detect_bos_choch,
    detect_fvg,
    detect_liquidity_sweeps
)

from app.strategies.signal_generator import (
    generate_trade_signals
)

from app.services.telegram_service import (
    send_alert
)

candles_df = pd.DataFrame()

def start_websocket():

    twm = ThreadedWebsocketManager()

    twm.start()

    def handle_socket_message(msg):

        global candles_df

        if msg['e'] == 'kline':

            candle = msg['k']

            # ONLY closed candles
            if candle['x']:

                new_row = {
                    "open": float(candle['o']),
                    "high": float(candle['h']),
                    "low": float(candle['l']),
                    "close": float(candle['c']),
                    "volume": float(candle['v'])
                }

                candles_df = pd.concat([
                    candles_df,
                    pd.DataFrame([new_row])
                ]).tail(200)

                print("\nNEW CLOSED CANDLE")
                print("Close:", candle['c'])

                # Need enough candles first
                if len(candles_df) > 20:

                    swing_highs, swing_lows = detect_swings(
                        candles_df
                    )

                    bos_signals = detect_bos_choch(
                        candles_df,
                        swing_highs,
                        swing_lows
                    )

                    fvgs = detect_fvg(
                        candles_df
                    )

                    sweeps = detect_liquidity_sweeps(
                        candles_df,
                        swing_highs,
                        swing_lows
                    )

                    trade_signals = generate_trade_signals(
                        bos_signals,
                        fvgs,
                        sweeps
                    )

                    # SEND LATEST SIGNAL
                    if trade_signals:

                        latest = trade_signals[-1]

                        message = f"""
LIVE SIGNAL

Signal: {latest['signal']}

Entry: {latest['entry']}
SL: {latest['sl']}
TP: {latest['tp']}
RR: {latest['rr']}
"""

                        print(message)
                        # No Telegram on signal generation — alerts fire only on
                        # actual trade open/close (trading_executor._notify_trade).

    twm.start_kline_socket(
        callback=handle_socket_message,
        symbol='btcusdt',
        interval='1m'
    )

    twm.join()