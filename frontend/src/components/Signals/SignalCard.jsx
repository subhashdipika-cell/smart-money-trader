function SignalCard({ account, market, signal }) {
  const isBuy = signal.signal === "BUY"

  const formatPrice = (value) => {
    const number = Number(value)
    return Number.isFinite(number)
      ? number.toLocaleString(undefined, { maximumFractionDigits: 2 })
      : "--"
  }

  const formatNumber = (value, maximumFractionDigits = 4) => {
    const number = Number(value)
    return Number.isFinite(number)
      ? number.toLocaleString(undefined, { maximumFractionDigits })
      : "--"
  }

  const capital = Number(account?.capital)
  const capitalCurrency = account?.capitalCurrency === "USD" ? "USD" : "INR"
  const riskPercent = Number(account?.riskPercent)
  const usdtInr = Number(account?.usdtInr)
  const entry = Number(signal.entry)
  const stop = Number(signal.sl)
  const target = Number(signal.tp)
  const stopDistance = Math.abs(entry - stop)
  const targetDistance = Math.abs(target - entry)
  const riskAmount = capital * (riskPercent / 100)
  const riskUsdt = capitalCurrency === "INR" && usdtInr > 0
    ? riskAmount / usdtInr
    : riskAmount
  const quantity = stopDistance > 0 ? riskUsdt / stopDistance : 0
  const lots = market?.lotSize ? quantity / market.lotSize : 0
  const notionalUsdt = quantity * entry
  const conversionRate = capitalCurrency === "INR" ? usdtInr : 1
  const notionalAmount = notionalUsdt * conversionRate
  const rewardAmount = quantity * targetDistance * conversionRate

  return (

    <div
      className={`signal-card ${isBuy ? "buy" : "sell"}`}
    >

      <div className="signal-card-header">
        <div>
          <span>{signal.signal || "WAIT"}</span>
          <small>{signal.timeframe || "Unmarked"} | {signal.confidence || "Unrated"}</small>
        </div>
        <strong>{signal.rr ? `${signal.rr}R` : "--"}</strong>
      </div>

      <dl>
        <div>
          <dt>Entry</dt>
          <dd>{formatPrice(signal.entry)}</dd>
        </div>

        <div>
          <dt>Stop</dt>
          <dd>{formatPrice(signal.sl)}</dd>
        </div>

        <div>
          <dt>Target</dt>
          <dd>{formatPrice(signal.tp)}</dd>
        </div>

        <div>
          <dt>Risk</dt>
          <dd>{capitalCurrency} {formatPrice(riskAmount)}</dd>
        </div>

        <div>
          <dt>Size</dt>
          <dd>{formatNumber(quantity)} {market?.unitLabel}</dd>
        </div>

        <div>
          <dt>Lots</dt>
          <dd>{formatNumber(lots)} {market?.lotLabel}</dd>
        </div>

        <div>
          <dt>Notional</dt>
          <dd>{capitalCurrency} {formatPrice(notionalAmount)}</dd>
        </div>

        <div>
          <dt>Est. reward</dt>
          <dd>{capitalCurrency} {formatPrice(rewardAmount)}</dd>
        </div>
      </dl>

      {Array.isArray(signal.confluences) && signal.confluences.length > 0 && (
        <div className="confluence-list">
          {signal.confluences.slice(0, 4).map((confluence) => (
            <span key={confluence}>{confluence}</span>
          ))}
        </div>
      )}

      <p className="signal-note">{market?.lotNote}</p>

    </div>

  )
}

export default SignalCard
