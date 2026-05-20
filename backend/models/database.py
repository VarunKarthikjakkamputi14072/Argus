import uuid
from datetime import datetime

from sqlalchemy import (
    Column,
    String,
    Text,
    Float,
    DateTime,
    ForeignKey,
    Index,
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, relationship
import enum


class Base(DeclarativeBase):
    pass


class TaskStatus(enum.Enum):
    PENDING = "pending"
    STARTED = "started"
    SCRAPING = "scraping"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Article(Base):
    __tablename__ = "articles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url = Column(String(2048), nullable=False, unique=True)
    title = Column(String(512), nullable=True)
    raw_content = Column(Text, nullable=True)
    source_domain = Column(String(256), nullable=True)
    word_count = Column(Float, nullable=True)
    status = Column(
        SAEnum(TaskStatus), nullable=False, default=TaskStatus.PENDING
    )
    celery_task_id = Column(String(256), nullable=True)
    error_message = Column(Text, nullable=True)

    scraped_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    metadata_entry = relationship(
        "ArticleMetadata", back_populates="article", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_articles_url", "url"),
        Index("ix_articles_status", "status"),
        Index("ix_articles_source_domain", "source_domain"),
        Index("ix_articles_created_at", "created_at"),
    )


class ArticleMetadata(Base):
    __tablename__ = "article_metadata"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    article_id = Column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    summary = Column(Text, nullable=True)
    entities = Column(JSONB, nullable=True)
    sentiment_score = Column(Float, nullable=True)
    sentiment_label = Column(String(32), nullable=True)
    llm_model_used = Column(String(128), nullable=True)
    token_usage = Column(JSONB, nullable=True)

    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    article = relationship("Article", back_populates="metadata_entry")

    __table_args__ = (
        Index("ix_metadata_article_id", "article_id"),
        Index("ix_metadata_sentiment_score", "sentiment_score"),
        Index("ix_metadata_processed_at", "processed_at"),
    )
