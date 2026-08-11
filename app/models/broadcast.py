from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from app.database import Base


class BroadcastHistory(Base):
    __tablename__ = "broadcast_history"

    id = Column(Integer, primary_key=True, index=True)

    channel = Column(String(20), index=True, nullable=False)  # sms | telegram
    message = Column(Text, nullable=False)

    total_recipients = Column(Integer, default=0, nullable=False)
    attempted = Column(Integer, default=0, nullable=False)
    sent = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)

    error_summary = Column(Text, nullable=True)

    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
