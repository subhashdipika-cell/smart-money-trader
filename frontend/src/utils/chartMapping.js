export function mapPriceToY(

  price,
  minPrice,
  maxPrice,
  chartHeight

) {

  const range = maxPrice - minPrice

  if (range === 0) {
    return chartHeight / 2
  }

  return (
    chartHeight -
    ((price - minPrice) / range)
      * chartHeight
  )

}

export function mapIndexToX(

  index,
  totalCandles,
  chartWidth

) {

  return (
    ((index || 0) / totalCandles)
      * chartWidth
  )

}
