import hashlib
import hmac
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

DB_PATH = "aether_core.db"
MAX_DAILY_TRAINS = 3
# Chiave di esempio per test SDM/SUN: in produzione mettila in variabile d'ambiente o chiave per-card
SDM_DEMO_SECRET = b"aether-core-sdm-demo-key-change-me"
ROOM_DUELS: dict[str, dict] = {}
ROOM_STATES: dict[str, dict] = {}

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


def today_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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
                card_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(rooms)").fetchall()]
        if "card_id" not in cols:
            conn.execute("ALTER TABLE rooms ADD COLUMN card_id TEXT")

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
                daily_trains INTEGER NOT NULL DEFAULT 0,
                last_train_date TEXT NOT NULL DEFAULT '',
                last_counter INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        c_cols = [r["name"] for r in conn.execute("PRAGMA table_info(cards)").fetchall()]
        if "daily_trains" not in c_cols:
            conn.execute("ALTER TABLE cards ADD COLUMN daily_trains INTEGER NOT NULL DEFAULT 0")
        if "last_train_date" not in c_cols:
            conn.execute("ALTER TABLE cards ADD COLUMN last_train_date TEXT NOT NULL DEFAULT ''")
        if "last_counter" not in c_cols:
            conn.execute("ALTER TABLE cards ADD COLUMN last_counter INTEGER NOT NULL DEFAULT 0")

        conn.commit()


def normalize_daily_trains(card: dict) -> dict:
    today = today_utc_str()
    if card.get("last_train_date") != today:
        card["daily_trains"] = 0
        card["last_train_date"] = today
    return card


def verify_sdm_if_present(conn, card: dict, counter_hex: str | None, cmac_hex: str | None) -> tuple[bool, int | None]:
    if not counter_hex and not cmac_hex:
        return True, None

    if not counter_hex or not cmac_hex:
        return False, None

    try:
        counter_val = int(counter_hex.strip(), 16)
    except ValueError:
        return False, None

    last_counter = int(card.get("last_counter") or 0)
    if counter_val <= last_counter:
        return False, counter_val

    expected_sig = hmac.new(
        SDM_DEMO_SECRET,
        f"{card['card_id']}:{counter_val:06X}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:16].upper()

    if not hmac.compare_digest(expected_sig, cmac_hex.strip().upper()):
        return False, counter_val

    return True, counter_val


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
        return normalize_daily_trains(dict(row))

    hero_key = default_hero_id.strip().lower()
    if hero_key not in BASE_ROSTER:
        hero_key = "sparky-bit"
    base = BASE_ROSTER[hero_key]
    now = datetime.now(timezone.utc).isoformat()
    today = today_utc_str()

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
        "daily_trains": 0,
        "last_train_date": today,
        "last_counter": 0,
        "updated_at": now,
    }
    conn.execute(
        """
        INSERT INTO cards (
            card_id, hero_id, forza, velocita, strategia, level, xp, max_xp,
            avail_points, daily_trains, last_train_date, last_counter, updated_at
        )
        VALUES (
            :card_id, :hero_id, :forza, :velocita, :strategia, :level, :xp, :max_xp,
            :avail_points, :daily_trains, :last_train_date, :last_counter, :updated_at
        )
        """,
        record,
    )
    conn.commit()
    return record


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Aether-Core API", version="1.4.0", lifespan=lifespan)

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
    cardId: str | None = None


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
    c: str | None = None
    m: str | None = None


class SpendPointRequest(BaseModel):
    stat: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/rooms")
def create_or_update_room(payload: RoomCreate):
    code = payload.code.strip().upper()
    hero_id = payload.heroId.strip().lower()
    card_id = payload.cardId.strip().upper() if payload.cardId else None
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO rooms (code, hero_id, card_id, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                hero_id = excluded.hero_id,
                card_id = excluded.card_id,
                created_at = excluded.created_at
            """,
            (code, hero_id, card_id, now),
        )
        conn.commit()

    ROOM_STATES[code] = {
        **ROOM_STATES.get(code, {}),
        "code": code,
        "p1Id": hero_id,
        "p1Card": card_id,
        "updatedAt": now,
    }
    return {"ok": True, "code": code, "heroId": hero_id, "cardId": card_id, "state": ROOM_STATES[code]}


@app.get("/api/rooms/{code}")
def get_room(code: str):
    clean_code = code.strip().upper()
    live_state = ROOM_STATES.get(clean_code)

    with get_conn() as conn:
        row = conn.execute(
            "SELECT code, hero_id, card_id, created_at FROM rooms WHERE code = ?",
            (clean_code,),
        ).fetchone()

    if not row and not live_state:
        raise HTTPException(status_code=404, detail="Stanza non trovata")

    base_hero = live_state.get("p1Id") if live_state else row["hero_id"]
    base_card = live_state.get("p1Card") if live_state else row["card_id"]
    created_at = row["created_at"] if row else live_state.get("updatedAt", datetime.now(timezone.utc).isoformat())

    return {
        "code": clean_code,
        "heroId": base_hero,
        "cardId": base_card,
        "createdAt": created_at,
        "state": live_state,
    }


@app.post("/api/rooms/{code}/state")
def set_room_state(code: str, payload: dict = Body(...)):
    clean_code = code.strip().upper()
    now = datetime.now(timezone.utc).isoformat()
    existing = ROOM_STATES.get(clean_code, {})
    merged = {**existing, **payload, "code": clean_code, "updatedAt": payload.get("updatedAt") or now}
    ROOM_STATES[clean_code] = merged

    p1_id = str(merged.get("p1Id") or "barkus").strip().lower()
    p1_card = merged.get("p1Card")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO rooms (code, hero_id, card_id, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                hero_id = excluded.hero_id,
                card_id = excluded.card_id
            """,
            (clean_code, p1_id, p1_card, now),
        )
        conn.commit()

    return {"ok": True, "code": clean_code, "state": merged}


@app.post("/api/rooms/{code}/duel")
def save_room_duel(code: str, payload: DuelResult):
    clean_code = code.strip().upper()
    now = datetime.now(timezone.utc).isoformat()

    with get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO rooms (code, hero_id, card_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (clean_code, payload.p1.lower(), payload.p1CardId, payload.timestamp),
        )

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
                        daily_trains = :daily_trains, last_train_date = :last_train_date,
                        last_counter = :last_counter, updated_at = :updated_at
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
def read_card(card_id: str, heroId: str = "sparky-bit", c: str | None = None, m: str | None = None):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        card = get_or_create_card(conn, card_id, heroId)
        ok_sdm, new_counter = verify_sdm_if_present(conn, card, c, m)
        if not ok_sdm:
            raise HTTPException(status_code=403, detail="Scansione NFC non valida o link già riutilizzato")

        if new_counter is not None:
            card["last_counter"] = new_counter
            card["updated_at"] = now
            conn.execute(
                """
                UPDATE cards
                SET daily_trains = :daily_trains, last_train_date = :last_train_date,
                    last_counter = :last_counter, updated_at = :updated_at
                WHERE card_id = :card_id
                """,
                card,
            )
            conn.commit()

    card["max_daily_trains"] = MAX_DAILY_TRAINS
    card["remaining_trains"] = max(0, MAX_DAILY_TRAINS - int(card["daily_trains"]))
    return card


@app.post("/api/cards/{card_id}/train")
def train_card(card_id: str, payload: TrainRequest):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        card = get_or_create_card(conn, card_id, payload.heroId or "sparky-bit")

        ok_sdm, new_counter = verify_sdm_if_present(conn, card, payload.c, payload.m)
        if not ok_sdm:
            raise HTTPException(status_code=403, detail="Scansione NFC non valida o link già riutilizzato")

        if int(card["daily_trains"]) >= MAX_DAILY_TRAINS:
            raise HTTPException(
                status_code=429,
                detail=f"Limite giornaliero raggiunto: massimo {MAX_DAILY_TRAINS} allenamenti al giorno",
            )

        card["daily_trains"] = int(card["daily_trains"]) + 1
        card["last_train_date"] = today_utc_str()
        if new_counter is not None:
            card["last_counter"] = new_counter

        card, leveled_up = apply_xp(card, payload.xpGain)
        card["updated_at"] = now
        conn.execute(
            """
            UPDATE cards
            SET forza = :forza, velocita = :velocita, strategia = :strategia,
                level = :level, xp = :xp, max_xp = :max_xp, avail_points = :avail_points,
                daily_trains = :daily_trains, last_train_date = :last_train_date,
                last_counter = :last_counter, updated_at = :updated_at
            WHERE card_id = :card_id
            """,
            card,
        )
        conn.commit()

    card["max_daily_trains"] = MAX_DAILY_TRAINS
    card["remaining_trains"] = max(0, MAX_DAILY_TRAINS - int(card["daily_trains"]))
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
