"""SQLite 数据层：建表、连接、数据文件定位。"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

SCHEMA_VERSION = 1

APP_NAME = "随机抽人"

SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL,
    sid        TEXT    NOT NULL DEFAULT '',
    cls        TEXT    NOT NULL DEFAULT '',
    enabled    INTEGER NOT NULL DEFAULT 1,
    drawn      INTEGER NOT NULL DEFAULT 0,
    drawn_at   TEXT,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS draws (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id INTEGER,
    seq        INTEGER NOT NULL,
    round      INTEGER NOT NULL,
    drawn_at   TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    sid        TEXT    NOT NULL DEFAULT '',
    cls        TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 大课时间区间。一天 4 个大课（上午两节、下午两节），
-- 区间含大课内部那 10 分钟课间（所以「课间算上课期间」由区间天然覆盖）。
CREATE TABLE IF NOT EXISTS blocks (
    idx   INTEGER PRIMARY KEY,   -- 1=上午① 2=上午② 3=下午① 4=下午②
    name  TEXT    NOT NULL,
    start TEXT    NOT NULL,      -- "08:00"
    end   TEXT    NOT NULL       -- "09:40"
);

-- 课表：某个班在星期几的某个大课有没有课。按大课记，不设小节层。
-- weeks 存「哪些教学周有课」的紧凑写法，如 "1-8,10-16"；空字符串 = 每周都有课。
CREATE TABLE IF NOT EXISTS schedule (
    cls       TEXT    NOT NULL,
    weekday   INTEGER NOT NULL,   -- 1=周一 … 7=周日
    block     INTEGER NOT NULL,   -- 对应 blocks.idx
    has_class INTEGER NOT NULL DEFAULT 1,
    weeks     TEXT    NOT NULL DEFAULT '',
    note      TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (cls, weekday, block)
);

-- 个别学生的例外（选修课等导致与全班不同），同样按大课记
CREATE TABLE IF NOT EXISTS schedule_exceptions (
    student_id INTEGER NOT NULL,
    weekday    INTEGER NOT NULL,
    block      INTEGER NOT NULL,
    has_class  INTEGER NOT NULL,  -- 1=额外有课，0=全班有课但他不上
    note       TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (student_id, weekday, block)
);

CREATE INDEX IF NOT EXISTS idx_students_pool ON students (enabled, drawn);
CREATE INDEX IF NOT EXISTS idx_draws_round   ON draws (round, seq);
-- 学生个人课表：某个学生自己的课表。若某学生在这张表里有记录，
-- 就完全用他自己的课表，不再用班级课表（教务是按学生导出的，选修课各不相同）。
CREATE TABLE IF NOT EXISTS student_schedule (
    student_id INTEGER NOT NULL,
    weekday    INTEGER NOT NULL,
    block      INTEGER NOT NULL,
    has_class  INTEGER NOT NULL DEFAULT 1,
    weeks      TEXT    NOT NULL DEFAULT '',
    note       TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (student_id, weekday, block)
);

CREATE INDEX IF NOT EXISTS idx_schedule_cls  ON schedule (cls, weekday);
CREATE INDEX IF NOT EXISTS idx_ssched_stu    ON student_schedule (student_id, weekday);
"""

# 默认作息。只在 blocks 表为空时写入，之后用户改过就不再覆盖。
DEFAULT_BLOCKS = [
    (1, "上午第一节大课", "08:00", "09:40"),
    (2, "上午第二节大课", "10:00", "11:40"),
    (3, "下午第一节大课", "14:00", "15:40"),
    (4, "下午第二节大课", "16:00", "17:40"),
    (5, "晚上第一节大课", "19:00", "20:40"),
    (6, "晚上第二节大课", "20:50", "22:30"),
]

# 教务课表里的节次段（第一二节、第三四节…）对应的序号。
# 导入时若对应的大课不存在，会按这里的默认为它建一个，时间可在界面上改。
BLOCK_DEFAULT_TIMES = {idx: (name, start, end)
                       for idx, name, start, end in DEFAULT_BLOCKS}

# 最短可谈时长（分钟）：离下个有课的大课不足这个数就不算空闲。
DEFAULT_MIN_TALK_MINUTES = 30


def _is_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def data_dir() -> Path:
    """数据文件目录。

    优先放在 exe（或脚本）同目录，便于整体拷贝迁移；
    若该目录不可写（例如装在 Program Files 或只读介质），退回 %APPDATA%。
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent

    if _is_writable(base):
        return base

    fallback = Path(os.environ.get("APPDATA") or Path.home()) / APP_NAME
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def db_path() -> Path:
    return data_dir() / "data.db"


# 老库缺的列：表已存在时 CREATE TABLE IF NOT EXISTS 不会补列，必须显式 ALTER。
# 加列只影响新数据，不动已有行，是安全的升级方式。
_MIGRATIONS = {
    "schedule": {"weeks": "TEXT NOT NULL DEFAULT ''"},
}


def ensure_blocks(conn: sqlite3.Connection, upto: int) -> None:
    """确保前 upto 个大课存在（导入教务课表时可能用到晚间时段）。"""
    have = {r["idx"] for r in conn.execute("SELECT idx FROM blocks")}
    for idx in range(1, upto + 1):
        if idx in have:
            continue
        name, start, end = BLOCK_DEFAULT_TIMES.get(
            idx, (f"第 {idx} 大课", "08:00", "09:40"))
        conn.execute(
            "INSERT INTO blocks (idx, name, start, end) VALUES (?, ?, ?, ?)",
            (idx, name, start, end),
        )
    conn.commit()


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _MIGRATIONS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not have:
            continue
        for name, decl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def connect(path: str | Path | None = None, check_same_thread: bool = False) -> sqlite3.Connection:
    """打开数据库连接。

    pywebview 与 HTTP 两种外壳都在别的线程上调用后端接口，
    因此默认允许跨线程使用，调用方需自行加锁串行化写操作。
    """
    conn = sqlite3.connect(str(path or db_path()), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = DELETE")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('round', '1')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta (key, value) VALUES ('min_talk_minutes', ?)",
        (str(DEFAULT_MIN_TALK_MINUTES),),
    )
    # 只在完全没有大课时写入默认作息：这样用户删掉某个大课之后
    # 不会被下次启动又补回来，改过的时间也不会被覆盖。
    _add_missing_columns(conn)
    if conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO blocks (idx, name, start, end) VALUES (?, ?, ?, ?)",
            DEFAULT_BLOCKS,
        )
    conn.commit()
