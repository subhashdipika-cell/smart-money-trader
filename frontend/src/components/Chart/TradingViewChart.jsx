const ADVANCED_CHART_BASE_URL =
  "https://www.tradingview-widget.com/embed-widget/advanced-chart/"

function TradingViewChart({ height = 640, symbol = "BINANCE:BTCUSDT" }) {
  const config = {
    autosize: true,
    symbol,
    interval: "5",
    timezone: "Asia/Kolkata",
    theme: "dark",
    style: "1",
    locale: "en",
    allow_symbol_change: true,
    support_host: "https://www.tradingview.com",
    backgroundColor: "#07111f",
    gridColor: "rgba(255, 255, 255, 0.06)",
    studies: [
      {
        "id": "MAExp@tv-basicstudies",
        "inputs": { "length": 13 },
        "override": { "Plot.color": "#2962FF", "Plot.linewidth": 1 }
      },
      {
        "id": "MAExp@tv-basicstudies",
        "inputs": { "length": 50 },
        "override": { "Plot.color": "#E81E86", "Plot.linewidth": 2 }
      },
      {
        "id": "MAExp@tv-basicstudies",
        "inputs": { "length": 200 },
        "override": { "Plot.color": "#DCD73C", "Plot.linewidth": 3 }
      }
    ],
    hide_side_toolbar: false,
    details: true,
    hotlist: false,
    withdateranges: true,
    width: "100%",
    height: "100%"
  }

  const src = `${ADVANCED_CHART_BASE_URL}?locale=en#${encodeURIComponent(
    JSON.stringify(config)
  )}`

  return (
    <iframe
      className="tradingview-advanced-chart"
      title="TradingView advanced chart"
      src={src}
      style={{ height }}
      allowFullScreen
    />
  )
}

export default TradingViewChart