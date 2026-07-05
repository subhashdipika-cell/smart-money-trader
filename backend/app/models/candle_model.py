from sqlalchemy import Column, Integer, String, Float, TIMESTAMP
from app.db.database import Base

class Candle(Base):

    __tablename__ = "candles"

    id = Column(Integer, primary_key=True, index=True)

    symbol = Column(String)
    timeframe = Column(String)

    timestamp = Column(TIMESTAMP)

    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)

    volume = Column(Float)