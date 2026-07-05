import {
  Stage,
  Layer,
  Rect,
  Text
} from "react-konva"

import {
  mapPriceToY,
  mapIndexToX
} from "../../utils/chartMapping"

function OverlayLayer({

  candles,

  fvgs,
  orderBlocks,
  height = 640

}) {

  if (!candles || candles.length === 0) {
    return null
  }

  const chartWidth = 1000
  const chartHeight = height
  const totalCandles = Math.max(candles.length - 1, 1)

  const prices = candles.flatMap(candle => [
    candle.high,
    candle.low
  ])

  const minPrice = Math.min(...prices)
  const maxPrice = Math.max(...prices)

  return (

    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: "100%",
        height,
        pointerEvents: "none",
        zIndex: 4
      }}
    >

      <Stage
        width={chartWidth}
        height={chartHeight}
        style={{
          width: "100%",
          height
        }}
      >

        <Layer>

          {/* ICT SESSION BOXES */}

          <Rect
            x={80}
            y={0}
            width={180}
            height={chartHeight}
            fill="#2563eb"
            opacity={0.08}
          />

          <Text
            x={130}
            y={22}
            text="ASIA"
            fontSize={20}
            fill="#60a5fa"
            fontStyle="bold"
            shadowColor="black"
            shadowBlur={6}
          />

          <Rect
            x={320}
            y={0}
            width={220}
            height={chartHeight}
            fill="#16a34a"
            opacity={0.08}
          />

          <Text
            x={375}
            y={22}
            text="LONDON"
            fontSize={20}
            fill="#4ade80"
            fontStyle="bold"
            shadowColor="black"
            shadowBlur={6}
          />

          <Rect
            x={620}
            y={0}
            width={240}
            height={chartHeight}
            fill="#f97316"
            opacity={0.08}
          />

          <Text
            x={650}
            y={18}
            text="NEW YORK"
            fontSize={20}
            fill="#fdba74"
            fontStyle="bold"
            shadowColor="black"
            shadowBlur={6}
          />

          {/* FVG ZONES */}

          {fvgs.slice(-8).map((fvg, index) => {

            const rawX = mapIndexToX(
              fvg.start ?? fvg.index,
              totalCandles,
              chartWidth
            )

            const x = Math.max(
              40,
              Math.min(rawX, chartWidth - 80)
            )

            const y = mapPriceToY(
              fvg.top,
              minPrice,
              maxPrice,
              chartHeight
            )

            const rawHeight = Math.abs(

              mapPriceToY(
                fvg.bottom,
                minPrice,
                maxPrice,
                chartHeight
              ) - y

            )

            const height = Math.max(
              20,
              Math.min(rawHeight, 80)
            )

            return (

              <Rect
                key={`fvg-${index}`}
                x={x}
                y={y}
                width={35}
                height={height}

                fill={
                  fvg.type === "bullish_fvg"
                    ? "#16a34a"
                    : "#dc2626"
                }

                opacity={0.24}

                stroke="white"
                strokeWidth={1}
              />

            )

          })}

          {/* ORDER BLOCKS */}

          {orderBlocks.slice(-6).map((ob, index) => {

            const rawX = mapIndexToX(
              ob.index,
              totalCandles,
              chartWidth
            )

            const x = Math.max(
              40,
              Math.min(rawX, chartWidth - 80)
            )

            const y = mapPriceToY(
              ob.top,
              minPrice,
              maxPrice,
              chartHeight
            )

            const rawHeight = Math.abs(

              mapPriceToY(
                ob.bottom,
                minPrice,
                maxPrice,
                chartHeight
              ) - y

            )

            const height = Math.max(
              20,
              Math.min(rawHeight, 100)
            )

            return (

              <Rect
                key={`ob-${index}`}
                x={x}
                y={y}
                width={45}
                height={height}

                fill={
                  ob.type === "bullish_ob"
                    ? "#2563eb"
                    : "#f59e0b"
                }

                opacity={0.18}

                stroke="white"
                strokeWidth={1}
              />

            )

          })}

        </Layer>

      </Stage>

    </div>

  )
}

export default OverlayLayer
