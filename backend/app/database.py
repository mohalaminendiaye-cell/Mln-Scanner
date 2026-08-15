"""Persistance : historique des scans, watchlist, et suivi de performance (backtest)."""
import logging
import os
from datetime import datetime, timedelta

from sqlmodel import SQLModel, Field, create_engine, Session, select

from .config import settings

logger = logging.getLogger("database")

# Pour une DB SQLite fichier, s'assure que le dossier parent existe (SQLite ne le
# crée pas lui-même) — pertinent notamment pour /app/data en environnement Docker.
if settings.DATABASE_URL.startswith("sqlite:///"):
    _db_path = settings.DATABASE_URL.replace("sqlite:///", "", 1).lstrip("/")
    _db_dir = os.path.dirname("/" + _db_path if settings.DATABASE_URL.startswith("sqlite:////") else _db_path)
    if _db_dir:
        os.makedirs(_db_dir, exist_ok=True)

engine = create_engine(settings.DATABASE_URL, connect_args={"check_same_thread": False})


class ScanRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    timestamp: datetime
    symbols_analyzed: int
    payload_json: str  # ScanResult sérialisé en JSON


class WatchlistRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    symbol: str = Field(index=True, unique=True)
    added_at: datetime


class SignalOutcomeRecord(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    scan_id: int | None = None
    symbol: str
    category: str
    exchange: str = "Binance"  # "Binance" | "Bybit" — détermine le client utilisé au monitoring
    direction: str
    entry: float
    stop_loss: float
    take_profit: float
    opened_at: datetime
    status: str = "pending"  # pending | win | loss | expired
    closed_at: datetime | None = None
    exit_price: float | None = None


def init_db():
    SQLModel.metadata.create_all(engine)
    _migrate_add_exchange_column()


def _migrate_add_exchange_column():
    """Migration légère : la colonne `exchange` a été ajoutée après la création
    initiale de la table signaloutcomerecord. `create_all` ne modifie pas les
    tables existantes, donc on l'ajoute nous-mêmes si absente (SQLite/Postgres).
    Sans cette migration, les bases créées avant cette mise à jour lèveraient une
    erreur "no such column: exchange" au premier enregistrement de signal."""
    from sqlalchemy import text, inspect
    inspector = inspect(engine)
    if "signaloutcomerecord" not in inspector.get_table_names():
        return
    columns = [c["name"] for c in inspector.get_columns("signaloutcomerecord")]
    if "exchange" in columns:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE signaloutcomerecord ADD COLUMN exchange VARCHAR DEFAULT 'Binance'"))
    logger.info("Migration DB: colonne 'exchange' ajoutée à signaloutcomerecord.")


# ---------------------------------------------------------------- Scans
def save_scan(scan_result_json: str, timestamp: datetime, symbols_analyzed: int) -> int:
    with Session(engine) as session:
        record = ScanRecord(timestamp=timestamp, symbols_analyzed=symbols_analyzed, payload_json=scan_result_json)
        session.add(record)
        session.commit()
        session.refresh(record)
        return record.id


def get_history(limit: int = 30) -> list[ScanRecord]:
    with Session(engine) as session:
        statement = select(ScanRecord).order_by(ScanRecord.timestamp.desc()).limit(limit)
        return list(session.exec(statement))


def get_scan_by_id(scan_id: int) -> ScanRecord | None:
    with Session(engine) as session:
        return session.get(ScanRecord, scan_id)


def get_latest_scan() -> ScanRecord | None:
    with Session(engine) as session:
        statement = select(ScanRecord).order_by(ScanRecord.timestamp.desc()).limit(1)
        return session.exec(statement).first()


def purge_old_scans(retention_days: int | None = None) -> int:
    """Supprime les scans (payload JSON complet) plus anciens que `retention_days`
    (settings.DB_RETENTION_DAYS par défaut). Les SignalOutcomeRecord (backtest) ne
    sont PAS purgés : ils sont légers et utiles pour l'historique de performance
    long terme. Retourne le nombre de scans supprimés."""
    days = retention_days if retention_days is not None else settings.DB_RETENTION_DAYS
    cutoff = datetime.utcnow() - timedelta(days=days)
    with Session(engine) as session:
        old_scans = list(session.exec(select(ScanRecord).where(ScanRecord.timestamp < cutoff)))
        for scan in old_scans:
            session.delete(scan)
        session.commit()
        if old_scans:
            logger.info(f"Purge DB: {len(old_scans)} scan(s) de plus de {days}j supprimé(s).")
        return len(old_scans)


# ---------------------------------------------------------------- Watchlist
def add_to_watchlist(symbol: str) -> WatchlistRecord | None:
    with Session(engine) as session:
        existing = session.exec(select(WatchlistRecord).where(WatchlistRecord.symbol == symbol)).first()
        if existing:
            return existing
        record = WatchlistRecord(symbol=symbol, added_at=datetime.utcnow())
        session.add(record)
        session.commit()
        session.refresh(record)
        return record


def remove_from_watchlist(symbol: str) -> bool:
    with Session(engine) as session:
        existing = session.exec(select(WatchlistRecord).where(WatchlistRecord.symbol == symbol)).first()
        if not existing:
            return False
        session.delete(existing)
        session.commit()
        return True


def get_watchlist() -> list[WatchlistRecord]:
    with Session(engine) as session:
        return list(session.exec(select(WatchlistRecord).order_by(WatchlistRecord.added_at.desc())))


# ---------------------------------------------------------------- Backtest / suivi de performance
def record_signal_outcomes(scan_id: int, signals: list, category: str):
    """Crée une ligne de suivi 'pending' pour chaque signal d'un scan (Cat.1, Cat.2,
    Cat.10...). `exchange` est lu sur le signal si présent (ex: Cat.10, multi-exchange),
    sinon "Binance" par défaut (Cat.1/Cat.2 sont Binance uniquement)."""
    with Session(engine) as session:
        for s in signals:
            session.add(
                SignalOutcomeRecord(
                    scan_id=scan_id,
                    symbol=s.symbol,
                    category=category,
                    exchange=getattr(s, "exchange", "Binance"),
                    direction=s.direction,
                    entry=s.entry,
                    stop_loss=s.stop_loss,
                    take_profit=s.take_profit,
                    opened_at=datetime.utcnow(),
                    status="pending",
                )
            )
        session.commit()


def get_pending_outcomes() -> list[SignalOutcomeRecord]:
    with Session(engine) as session:
        return list(session.exec(select(SignalOutcomeRecord).where(SignalOutcomeRecord.status == "pending")))


def close_outcome(outcome_id: int, status: str, exit_price: float):
    with Session(engine) as session:
        record = session.get(SignalOutcomeRecord, outcome_id)
        if record:
            record.status = status
            record.exit_price = exit_price
            record.closed_at = datetime.utcnow()
            session.add(record)
            session.commit()


def _apply_backtest_filters(statement, category: str | None, period: str | None):
    if category:
        statement = statement.where(SignalOutcomeRecord.category == category)
    if period and period != "all":
        now = datetime.utcnow()
        cutoffs = {"day": timedelta(days=1), "week": timedelta(days=7), "month": timedelta(days=30)}
        cutoff = cutoffs.get(period)
        if cutoff:
            statement = statement.where(SignalOutcomeRecord.opened_at >= now - cutoff)
    return statement


def get_backtest_categories() -> list[str]:
    """Liste des catégories effectivement présentes dans l'historique de
    backtest, pour peupler le filtre côté frontend."""
    with Session(engine) as session:
        rows = session.exec(select(SignalOutcomeRecord.category).distinct())
        return sorted({r for r in rows if r})


def get_backtest_stats(category: str | None = None, period: str | None = None) -> dict:
    with Session(engine) as session:
        statement = select(SignalOutcomeRecord).where(SignalOutcomeRecord.status != "pending")
        statement = _apply_backtest_filters(statement, category, period)
        closed = list(session.exec(statement))
    total = len(closed)
    wins = len([c for c in closed if c.status == "win"])
    losses = len([c for c in closed if c.status == "loss"])
    expired = len([c for c in closed if c.status == "expired"])
    win_rate = round(wins / total * 100, 1) if total else 0.0

    by_category: dict[str, dict[str, float]] = {}
    for cat in set(c.category for c in closed):
        cat_closed = [c for c in closed if c.category == cat]
        cat_wins = len([c for c in cat_closed if c.status == "win"])
        by_category[cat] = {
            "total": len(cat_closed),
            "wins": cat_wins,
            "win_rate_pct": round(cat_wins / len(cat_closed) * 100, 1) if cat_closed else 0.0,
        }

    return {
        "total_closed": total,
        "wins": wins,
        "losses": losses,
        "expired": expired,
        "win_rate_pct": win_rate,
        "by_category": by_category,
    }


def get_recent_outcomes(
    limit: int = 20, category: str | None = None, period: str | None = None
) -> list[SignalOutcomeRecord]:
    with Session(engine) as session:
        statement = select(SignalOutcomeRecord).where(SignalOutcomeRecord.status != "pending")
        statement = _apply_backtest_filters(statement, category, period)
        statement = statement.order_by(SignalOutcomeRecord.closed_at.desc()).limit(limit)
        return list(session.exec(statement))
