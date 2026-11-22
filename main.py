import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DB_PATH = "aether_core.db"
ROOM_DUELS: dict[str, dict] = {}

BASE_ROSTER = {
    "sparky-bit": {"name": "SPARKY-BIT", "faction": "VOLT-TECH", "forza": 9, "velocita": 15, "strategia": 9, "level": 4, "xp": 130, "max_xp": 200, "avail_points": 2},
    "root": {"name": "ROOT", "faction": "VOLT-TECH", "forza": 7, "velocita": 11, "strategia": 14, "level": 3, "xp": 110, "max_xp": 150, "avail_points": 2},
    "kilowatt": {"name": "KILOWATT", "faction": "VOLT-TECH", "forza": 14, "velocita": 7, "strategia": 9, "level": 2, "xp": 50, "max_xp": 100, "avail_points": 2},
    "krag-core": {"name": "KRAG-CORE", "faction": "MAGMA-CORE", "forza": 15, "velocita": 6, "strategia": 9, "level": 2, "xp": 50, "max_xp": 100, "avail_points": 2},
    "blaze": {"name": "BLAZE", "faction": "MAGMA-CORE", "forza": 8, "velocita": 14, "strategia": 8, "level": 2, "xp": 50, "max_xp": 100, "avail_points": 2},
    "vulkan": {"name": "VULKAN", "faction": "MAGMA-CORE", "forza": 15, "velocita": 7, "strategia": 8, "level": 2, "xp": 40, "max_xp": 100, "avail_points": 2},
    "hornet": {"name": "HORNET", "faction": "BIO-WILD", "forza": 9, "velocita": 15, "strategia": 6, "level": 2, "xp": 50, "max_xp": 100, "avail_points": 2},
    "roxy": {"name": "ROXY", "faction": "BIO-WILD", "forza": 7, "velocita": 11, "strategia": 13, "level": 2, "xp": 50, "max_xp": 100, "avail_points": 2},
    "barkus": {"name": "BARKUS", "faction": "BIO-WILD", "forza": 14, "velocita": 7, "strategia": 9, "level": 2, "xp": 50, "max_xp": 100, "avail_points": 2},
    "cryo-spectre": {"name": "CRYO-SPECTRE", "faction": "CYBER-FROST", "forza": 7, "velocita": 11, "strategia": 12, "level": 2, "xp": 50, "max_xp": 100, "avail_points": 2},
    "blizzard": {"name": "BLIZZARD", "faction": "CYBER-FROST", "forza": 13, "velocita": 8, "strategia": 9, "level": 2, "xp": 50, "max_xp": 100, "avail_points": 2},
    "shiver": {"name": "SHIVER", "faction": "CYBER-FROST", "forza": 8, "velocita": 15, "strategia": 8, "level": 2, "xp": 50, "max_xp": 100, "avail_points": 2},
}


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rooms (
                code TEXT PRIMARY KEY,
                hero_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cards (
                card_id TEXT PRIMARY KEY,
                hero_id TEXT NOT NULL,
                forza INTEGER NOT NULL,
                velocita INTEGER NOT NULL,
                strategia INTEGER NOT NULL,
                level INTEGER NOT NULL,
                xp INTEGER NOT NULL,
                max_xp INTEGER NOT NULL,
                avail_points INTEGER NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


def apply_xp(row_dict: dict, gained_xp: int) -> tuple[dict, bool]:
    xp = row_dict["xp"] + max(0, gained_xp)
    level = row_dict["level"]
    max_xp = row_dict["max_xp"]
    avail_points = row_dict["avail_points"]
    leveled_up = False

    while xp >= max_xp:
        xp -= max_xp
        level += 1
        max_xp += 50
        avail_points += 1
        leveled_up = True

    row_dict["xp"] = xp
    row_dict["level"] = level
    row_dict["max_xp"] = max_xp
    row_dict["avail_points"] = avail_points
    return row_dict, leveled_up


def get_or_create_card(conn, card_id: str, default_hero_id: str = "sparky-bit") -> dict:
    clean_card = card_id.strip().upper()
    row = conn.execute("SELECT * FROM cards WHERE card_id = ?", (clean_card,)).fetchone()
    if row:
        return dict(row)

    hero_key = default_hero_id.strip().lower()
    if hero_key not in BASE_ROSTER:
        hero_key = "sparky-bit"
    base = BASE_ROSTER[hero_key]
    now = datetime.now(timezone.utc).isoformat()

    record = {
        "card_id": clean_card,
        "hero_id": hero_key,
        "forza": base["forza"],
        "velocita": base["velocita"],
        "strategia": base["strategia"],
        "level": base["level"],
        "xp": base["xp"],
        "max_xp": base["max_xp"],
        "avail_points": base["avail_points"],
        "updated_at": now,
    }
    conn.execute(
        """
        INSERT INTO cards (card_id, hero_id, forza, velocita, strategia, level, xp, max_xp, avail_points, updated_at)
        VALUES (:card_id, :hero_id, :forza, :velocita, :strategia, :level, :xp, :max_xp, :avail_points, :updated_at)
        """,
        record,
    )
    conn.commit()
    return record


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Aether-Core API", version="1.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://layertap3d.com",
        "https://www.layertap3d.com",
        "http://localhost",
        "http://127.0.0.1",
        "null",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


class RoomCreate(BaseModel):
    code: str = Field(..., min_length=3, max_length=16)
    heroId: str = Field(..., min_length=2, max_length=40)


class DuelResult(BaseModel):
    p1: str
    p2: str
    p1CardId: str | None = None
    p2CardId: str | None = None
    p1Move: str
    p2Move: str
    power1: int
    power2: int
    winner: str
    timestamp: str


class TrainRequest(BaseModel):
    heroId: str | None = None
    xpGain: int = Field(default=25, ge=5, le=100)


class SpendPointRequest(BaseModel):
    stat: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/rooms")
def create_or_update_room(payload: RoomCreate):
    code = payload.code.strip().upper()
    hero_id = payload.heroId.strip().lower()
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO rooms (code, hero_id, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                hero_id = excluded.hero_id,
                created_at = excluded.created_at
            """,
            (code, hero_id, now),
        )
        conn.commit()

    return {"ok": True, "code": code, "heroId": hero_id}


@app.get("/api/rooms/{code}")
def get_room(code: str):
    clean_code = code.strip().upper()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT code, hero_id, created_at FROM rooms WHERE code = ?",
            (clean_code,),
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Stanza non trovata")

    return {
        "code": row["code"],
        "heroId": row["hero_id"],
        "createdAt": row["created_at"],
    }


@app.post("/api/rooms/{code}/duel")
def save_room_duel(code: str, payload: DuelResult):
    clean_code = code.strip().upper()
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO rooms (code, hero_id, created_at)
            VALUES (?, ?, ?)
            """,
            (clean_code, payload.p1.lower(), payload.timestamp),
        )

        # Aggiorna XP delle card collegate, se presenti
        for cid, hid, xp_amt in [
            (payload.p1CardId, payload.p1, 40 if payload.winner.lower() == payload.p1.lower() else 15),
            (payload.p2CardId, payload.p2, 40 if payload.winner.lower() == payload.p2.lower() else 15),
        ]:
            if cid:
                card = get_or_create_card(conn, cid, hid)
                card, _ = apply_xp(card, xp_amt)
                card["updated_at"] = now
                conn.execute(
                    """
                    UPDATE cards
                    SET forza = :forza, velocita = :velocita, strategia = :strategia,
                        level = :level, xp = :xp, max_xp = :max_xp, avail_points = :avail_points,
                        updated_at = :updated_at
                    WHERE card_id = :card_id
                    """,
                    card,
                )
        conn.commit()

    ROOM_DUELS[clean_code] = payload.model_dump()
    return {"ok": True, "code": clean_code, "duel": ROOM_DUELS[clean_code]}


@app.get("/api/rooms/{code}/duel")
def get_room_duel(code: str):
    clean_code = code.strip().upper()
    duel = ROOM_DUELS.get(clean_code)
    return {"code": clean_code, "duel": duel}


@app.get("/api/cards/{card_id}")
def read_card(card_id: str, heroId: str = "sparky-bit"):
    with get_conn() as conn:
        card = get_or_create_card(conn, card_id, heroId)
    return card


@app.post("/api/cards/{card_id}/train")
def train_card(card_id: str, payload: TrainRequest):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        card = get_or_create_card(conn, card_id, payload.heroId or "sparky-bit")
        card, leveled_up = apply_xp(card, payload.xpGain)
        card["updated_at"] = now
        conn.execute(
            """
            UPDATE cards
            SET forza = :forza, velocita = :velocita, strategia = :strategia,
                level = :level, xp = :xp, max_xp = :max_xp, avail_points = :avail_points,
                updated_at = :updated_at
            WHERE card_id = :card_id
            """,
            card,
        )
        conn.commit()
    return {"ok": True, "leveledUp": leveled_up, "card": card}


@app.post("/api/cards/{card_id}/spend-point")
def spend_card_point(card_id: str, payload: SpendPointRequest):
    stat = payload.stat.strip().lower()
    if stat not in ("forza", "velocita", "strategia"):
        raise HTTPException(status_code=400, detail="Statistica non valida")

    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        card = get_or_create_card(conn, card_id)
        if card["avail_points"] <= 0:
            raise HTTPException(status_code=400, detail="Nessun punto abilità disponibile")

        card[stat] = min(20, card[stat] + 1)
        card["avail_points"] -= 1
        card["updated_at"] = now
        conn.execute(
            """
            UPDATE cards
            SET forza = :forza, velocita = :velocita, strategia = :strategia,
                avail_points = :avail_points, updated_at = :updated_at
            WHERE card_id = :card_id
            """,
            card,
        )
        conn.commit()
    return {"ok": True, "card": card}
