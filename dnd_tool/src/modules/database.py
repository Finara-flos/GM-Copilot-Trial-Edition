"""SQLite 数据层：模组分段、关键词、任务进度与术语替换记录。"""
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "gm_copilot.db"


def configure_default_path(path: str | Path) -> None:
    """设置当前登录账户的默认 SQLite 数据库路径。"""
    global DEFAULT_DB_PATH
    DEFAULT_DB_PATH = Path(path)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chapter TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    original_file TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_file ON segments(original_file);

CREATE TABLE IF NOT EXISTS keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_file TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    UNIQUE(original_file, name)
);
CREATE INDEX IF NOT EXISTS idx_keywords_file ON keywords(original_file);

CREATE TABLE IF NOT EXISTS task_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_type TEXT NOT NULL,
    original_file TEXT NOT NULL,
    chapter TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'done',
    updated_at TEXT NOT NULL DEFAULT '',
    UNIQUE(task_type, original_file, chapter)
);

CREATE TABLE IF NOT EXISTS term_replacements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name TEXT NOT NULL,
    original TEXT NOT NULL,
    replacement TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS expanded_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    segment_id INTEGER NOT NULL,
    version_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    is_selected INTEGER NOT NULL DEFAULT 0,
    UNIQUE(segment_id, version_index)
);
CREATE INDEX IF NOT EXISTS idx_expanded_segment ON expanded_versions(segment_id);

CREATE TABLE IF NOT EXISTS npcs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    original_file TEXT NOT NULL DEFAULT '',
    motivation TEXT NOT NULL DEFAULT '',
    secret TEXT NOT NULL DEFAULT '',
    catchphrase TEXT NOT NULL DEFAULT '',
    flaw TEXT NOT NULL DEFAULT '',
    appearance TEXT NOT NULL DEFAULT '',
    backstory TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS npc_dialogues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    npc_name TEXT NOT NULL,
    scene TEXT NOT NULL,
    line TEXT NOT NULL,
    UNIQUE(npc_name, scene)
);

CREATE TABLE IF NOT EXISTS relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_file TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    target TEXT NOT NULL,
    rel_type TEXT NOT NULL DEFAULT '盟友'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_relationships_uniq
    ON relationships(original_file, source, target, rel_type);
CREATE INDEX IF NOT EXISTS idx_relationships_file ON relationships(original_file);
"""


class Database:
    """SQLite 访问层；线程安全（导入在后台线程写入，UI 线程读取）。"""

    def __init__(self, db_path: str | Path | None = None):
        self._path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # 跨线程共享连接需关闭线程检查；写操作由锁串行化
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # 多连接写锁竞争时最多等待 5 秒而非立即报错
        self._conn.execute("PRAGMA busy_timeout = 5000")
        with self._lock:
            self._migrate_keywords()
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self._migrate_relationships()

    # ---- segments ----
    def _migrate_keywords(self) -> None:
        """将旧版全局关键词表迁移为按模组归属的关键词表。"""
        columns = self._conn.execute("PRAGMA table_info('keywords')").fetchall()
        if not columns or any(row["name"] == "original_file" for row in columns):
            return
        rows = self._conn.execute(
            "SELECT name, kind, detail FROM keywords ORDER BY id").fetchall()
        files = [row["original_file"] for row in self._conn.execute(
            "SELECT original_file FROM segments GROUP BY original_file ORDER BY MIN(id)"
        ).fetchall()]
        try:
            self._conn.executescript(
                "DROP TABLE keywords;\n"
                "CREATE TABLE keywords (\n"
                "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
                "    original_file TEXT NOT NULL DEFAULT '',\n"
                "    name TEXT NOT NULL,\n"
                "    kind TEXT NOT NULL,\n"
                "    detail TEXT NOT NULL DEFAULT '',\n"
                "    UNIQUE(original_file, name)\n"
                ");\n"
                "CREATE INDEX idx_keywords_file ON keywords(original_file);"
            )
            if files:
                self._conn.executemany(
                    "INSERT OR IGNORE INTO keywords (original_file, name, kind, detail) "
                    "VALUES (?, ?, ?, ?)",
                    [(file_name, row["name"], row["kind"], row["detail"])
                     for file_name in files for row in rows],
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ---- segments ----
    def _migrate_relationships(self) -> None:
        """把旧版 relationships 的唯一约束（不含 original_file）迁移到新约束。

        旧表通过表内 UNIQUE 约束去重；新表用命名唯一索引（含 original_file）。
        检测到旧约束索引时删除并重建，避免跨文件同名关系冲突。
        """
        try:
            indexes = self._conn.execute(
                "PRAGMA index_list('relationships')").fetchall()
            for row in indexes:
                idx_name = row["name"]
                # 旧版自动生成的 UNIQUE 索引名形如 sqlite_autoindex_relationships_1
                if idx_name.startswith("sqlite_autoindex_relationships") \
                        and idx_name != "idx_relationships_uniq":
                    # 重建表：数据备份后重放
                    data = self._conn.execute(
                        "SELECT original_file, source, target, rel_type "
                        "FROM relationships").fetchall()
                    self._conn.executescript(
                        "DROP TABLE relationships;\n" + _SCHEMA)
                    self._conn.executemany(
                        "INSERT INTO relationships "
                        "(original_file, source, target, rel_type) VALUES (?, ?, ?, ?)",
                        [tuple(r) for r in data],
                    )
                    self._conn.commit()
                    return
        except Exception:  # noqa: BLE001  迁移失败不阻断（下次启动重试）
            try:
                self._conn.rollback()
            except Exception:  # noqa: BLE001
                pass

    def insert_segments(self, segments: list[dict], original_file: str) -> int:
        """批量插入分段，返回插入条数。"""
        with self._lock:
            try:
                cur = self._conn.executemany(
                    "INSERT INTO segments (chapter, content, original_file) VALUES (?, ?, ?)",
                    [(s.get("chapter", "未分章"), s.get("content", ""), original_file)
                     for s in segments],
                )
                self._conn.commit()
                return cur.rowcount
            except Exception:
                self._conn.rollback()
                raise

    def clear_file(self, original_file: str) -> None:
        """删除某模组的所有派生数据，供重新导入和删除模组共用。"""
        with self._lock:
            try:
                npc_rows = self._conn.execute(
                    "SELECT name FROM npcs WHERE original_file = ?", (original_file,)
                ).fetchall()
                npc_names = [row["name"] for row in npc_rows]
                if npc_names:
                    placeholders = ", ".join("?" for _ in npc_names)
                    self._conn.execute(
                        f"DELETE FROM npc_dialogues WHERE npc_name IN ({placeholders})", npc_names)
                self._conn.execute(
                    "DELETE FROM expanded_versions WHERE segment_id IN "
                    "(SELECT id FROM segments WHERE original_file = ?)",
                    (original_file,),
                )
                self._conn.execute("DELETE FROM keywords WHERE original_file = ?", (original_file,))
                self._conn.execute("DELETE FROM task_progress WHERE original_file = ?", (original_file,))
                self._conn.execute("DELETE FROM npcs WHERE original_file = ?", (original_file,))
                self._conn.execute("DELETE FROM relationships WHERE original_file = ?", (original_file,))
                self._conn.execute(
                    "DELETE FROM segments WHERE original_file = ?", (original_file,))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get_segments(self, original_file: str | None = None) -> list[dict]:
        """按插入顺序返回分段列表；不传文件时返回全部。"""
        with self._lock:
            if original_file is None:
                rows = self._conn.execute(
                    "SELECT id, chapter, content, original_file FROM segments ORDER BY id"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, chapter, content, original_file FROM segments "
                    "WHERE original_file = ? ORDER BY id",
                    (original_file,),
                ).fetchall()
        return [dict(r) for r in rows]

    def files(self) -> list[str]:
        """已导入的文件名列表（按首次导入时间排序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT original_file FROM segments GROUP BY original_file ORDER BY MIN(id)"
            ).fetchall()
        return [r["original_file"] for r in rows]

    def count_segments(self, original_file: str | None = None) -> int:
        """分段总数；不传文件时统计全部。"""
        with self._lock:
            if original_file is None:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM segments").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM segments WHERE original_file = ?",
                    (original_file,),
                ).fetchone()
        return row["n"]

    def update_segment_content(self, segment_id: int, content: str) -> None:
        """更新单个分段的正文（术语替换/隐喻转译后调用）。"""
        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE segments SET content = ? WHERE id = ?",
                    (content, segment_id),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- keywords ----
    def add_keyword(self, name: str, kind: str, detail: str = "",
                    original_file: str = "") -> bool:
        """新增某模组的关键词；该模组内重名返回 False。"""
        name = name.strip()
        if not name or kind not in ("npc", "place", "item"):
            return False
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO keywords (original_file, name, kind, detail) VALUES (?, ?, ?, ?)",
                    (original_file, name, kind, detail),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                self._conn.rollback()
                return False

    def remove_keyword(self, name: str, original_file: str = "") -> None:
        """删除某模组的关键词。"""
        with self._lock:
            try:
                self._conn.execute(
                    "DELETE FROM keywords WHERE original_file = ? AND name = ?",
                    (original_file, name),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get_keywords(self, original_file: str | None = None) -> list[dict]:
        """返回指定模组关键词；不传时返回全部（兼容汇总场景）。"""
        with self._lock:
            if original_file is None:
                rows = self._conn.execute(
                    "SELECT original_file, name, kind, detail FROM keywords ORDER BY name"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT original_file, name, kind, detail FROM keywords "
                    "WHERE original_file = ? ORDER BY name",
                    (original_file,),
                ).fetchall()
        return [dict(r) for r in rows]

    def get_keyword(self, name: str, original_file: str | None = None) -> dict | None:
        """按名称查询关键词详情；优先限定当前模组。"""
        with self._lock:
            if original_file is None:
                row = self._conn.execute(
                    "SELECT original_file, name, kind, detail FROM keywords "
                    "WHERE name = ? ORDER BY id LIMIT 1", (name,)
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT original_file, name, kind, detail FROM keywords "
                    "WHERE original_file = ? AND name = ?",
                    (original_file, name),
                ).fetchone()
        return dict(row) if row else None

    # ---- 任务进度（断点续传） ----
    def is_task_done(self, task_type: str, file_name: str, chapter: str) -> bool:
        """某章节任务是否已完成。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM task_progress WHERE task_type=? AND original_file=? AND chapter=?",
                (task_type, file_name, chapter),
            ).fetchone()
        return bool(row and row["status"] == "done")

    def mark_task_done(self, task_type: str, file_name: str, chapter: str) -> None:
        """记录某章节任务完成。"""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO task_progress "
                    "(task_type, original_file, chapter, status, updated_at) VALUES (?, ?, ?, 'done', ?)",
                    (task_type, file_name, chapter,
                     datetime.now().isoformat(timespec="seconds")),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def clear_task_progress(self, task_type: str | None = None,
                            file_name: str | None = None) -> None:
        """清除任务进度（重新执行时调用）。"""
        with self._lock:
            try:
                sql = "DELETE FROM task_progress WHERE 1=1"
                args: list = []
                if task_type:
                    sql += " AND task_type=?"
                    args.append(task_type)
                if file_name:
                    sql += " AND original_file=?"
                    args.append(file_name)
                self._conn.execute(sql, args)
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- 术语替换记录 ----
    def add_term_replacement(self, group_name: str, original: str,
                             replacement: str) -> int:
        """记录一条术语替换，返回记录 id。"""
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT INTO term_replacements (group_name, original, replacement, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (group_name, original, replacement,
                     datetime.now().isoformat(timespec="seconds")),
                )
                self._conn.commit()
                return cur.lastrowid
            except Exception:
                self._conn.rollback()
                raise

    def get_term_replacements(self) -> list[dict]:
        """全部术语替换记录。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, group_name, original, replacement, created_at "
                "FROM term_replacements ORDER BY id"
            ).fetchall()
        return [dict(r) for r in rows]

    def remove_term_replacement(self, replacement_id: int) -> None:
        """删除一条术语替换记录（撤销）。"""
        with self._lock:
            try:
                self._conn.execute(
                    "DELETE FROM term_replacements WHERE id = ?", (replacement_id,))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- 扩写版本 ----
    def has_expanded(self, segment_id: int) -> bool:
        """该分段是否已有扩写版本（断点续传判断）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM expanded_versions WHERE segment_id = ?",
                (segment_id,),
            ).fetchone()
        return bool(row and row["n"] > 0)

    def save_expanded_versions(
        self, segment_id: int, versions: list[str], select_index: int = 0
    ) -> None:
        """保存某段落的多个扩写版本；select_index 为默认采纳的版本号。"""
        with self._lock:
            try:
                self._conn.execute(
                    "DELETE FROM expanded_versions WHERE segment_id = ?",
                    (segment_id,),
                )
                self._conn.executemany(
                    "INSERT INTO expanded_versions "
                    "(segment_id, version_index, content, is_selected) VALUES (?, ?, ?, ?)",
                    [(segment_id, i, content, 1 if i == select_index else 0)
                     for i, content in enumerate(versions)],
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get_expanded_versions(self, segment_id: int) -> list[dict]:
        """该分段的扩写版本列表（按版本号排序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT version_index, content, is_selected FROM expanded_versions "
                "WHERE segment_id = ? ORDER BY version_index",
                (segment_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_selected_version(self, segment_id: int) -> str | None:
        """该分段当前采纳的扩写版本内容；无则返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT content FROM expanded_versions "
                "WHERE segment_id = ? AND is_selected = 1",
                (segment_id,),
            ).fetchone()
        return row["content"] if row else None

    def select_expanded_version(self, segment_id: int, version_index: int) -> None:
        """切换某分段采纳的版本。"""
        with self._lock:
            try:
                self._conn.execute(
                    "UPDATE expanded_versions SET is_selected = 0 WHERE segment_id = ?",
                    (segment_id,),
                )
                self._conn.execute(
                    "UPDATE expanded_versions SET is_selected = 1 "
                    "WHERE segment_id = ? AND version_index = ?",
                    (segment_id, version_index),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def clear_expanded(self, file_name: str | None = None) -> None:
        """清除扩写版本（可选按文件：通过 segments 联查）。"""
        with self._lock:
            try:
                if file_name:
                    self._conn.execute(
                        "DELETE FROM expanded_versions WHERE segment_id IN "
                        "(SELECT id FROM segments WHERE original_file = ?)",
                        (file_name,),
                    )
                else:
                    self._conn.execute("DELETE FROM expanded_versions")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        """关闭连接。"""
        with self._lock:
            self._conn.close()

    # ---- NPC 档案 ----
    def upsert_npc(self, npc: dict, original_file: str = "") -> None:
        """新增或更新 NPC 档案（按名称唯一）。"""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO npcs (name, original_file, motivation, secret, "
                    "catchphrase, flaw, appearance, backstory, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET "
                    "original_file=excluded.original_file, "
                    "motivation=excluded.motivation, secret=excluded.secret, "
                    "catchphrase=excluded.catchphrase, flaw=excluded.flaw, "
                    "appearance=excluded.appearance, backstory=excluded.backstory, "
                    "updated_at=excluded.updated_at",
                    (
                        npc.get("name", "").strip(), original_file,
                        npc.get("motivation", ""), npc.get("secret", ""),
                        npc.get("catchphrase", ""), npc.get("flaw", ""),
                        npc.get("appearance", ""), npc.get("backstory", ""),
                        datetime.now().isoformat(timespec="seconds"),
                    ),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get_npcs(self, original_file: str | None = None) -> list[dict]:
        """NPC 列表；不传文件返回全部。"""
        with self._lock:
            if original_file:
                rows = self._conn.execute(
                    "SELECT * FROM npcs WHERE original_file = ? ORDER BY name",
                    (original_file,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM npcs ORDER BY name").fetchall()
        return [dict(r) for r in rows]

    def get_npc(self, name: str) -> dict | None:
        """按名称查询 NPC 档案。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM npcs WHERE name = ?", (name,)).fetchone()
        return dict(row) if row else None

    def update_npc(self, name: str, fields: dict) -> None:
        """按名称更新 NPC 档案的指定字段。"""
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        args = list(fields.values())
        with self._lock:
            try:
                self._conn.execute(
                    f"UPDATE npcs SET {sets}, updated_at=? WHERE name=?",
                    args + [datetime.now().isoformat(timespec="seconds"), name],
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def clear_npcs(self, original_file: str | None = None) -> None:
        """清除 NPC（可选按文件）。"""
        with self._lock:
            try:
                if original_file:
                    self._conn.execute(
                        "DELETE FROM npcs WHERE original_file = ?", (original_file,))
                else:
                    self._conn.execute("DELETE FROM npcs")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- NPC 台词 ----
    def save_npc_dialogue(self, npc_name: str, scene: str, line: str) -> None:
        """保存某 NPC 某场景的一句台词。"""
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO npc_dialogues (npc_name, scene, line) VALUES (?, ?, ?) "
                    "ON CONFLICT(npc_name, scene) DO UPDATE SET line=excluded.line",
                    (npc_name, scene, line),
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get_npc_dialogues(self, npc_name: str) -> list[dict]:
        """某 NPC 的全部台词（按场景）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT scene, line FROM npc_dialogues WHERE npc_name = ? ORDER BY id",
                (npc_name,),
            ).fetchall()
        return [dict(r) for r in rows]

    def clear_npc_dialogues(self, npc_name: str | None = None) -> None:
        """清除台词（可选按 NPC）。"""
        with self._lock:
            try:
                if npc_name:
                    self._conn.execute(
                        "DELETE FROM npc_dialogues WHERE npc_name = ?", (npc_name,))
                else:
                    self._conn.execute("DELETE FROM npc_dialogues")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ---- NPC 关系 ----
    def add_relationship(self, source: str, target: str, rel_type: str,
                         original_file: str = "") -> bool:
        """新增一条关系（去重）；rel_type: 盟友/敌对/恋人/秘密。"""
        if not source or not target or source == target:
            return False
        if rel_type not in ("盟友", "敌对", "恋人", "秘密"):
            return False
        with self._lock:
            try:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO relationships "
                    "(original_file, source, target, rel_type) VALUES (?, ?, ?, ?)",
                    (original_file, source, target, rel_type),
                )
                self._conn.commit()
                return cur.rowcount > 0
            except Exception:
                self._conn.rollback()
                raise

    def get_relationships(self, original_file: str | None = None) -> list[dict]:
        """全部关系；不传文件返回全部。"""
        with self._lock:
            if original_file:
                rows = self._conn.execute(
                    "SELECT source, target, rel_type FROM relationships "
                    "WHERE original_file = ? ORDER BY id",
                    (original_file,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT source, target, rel_type FROM relationships ORDER BY id"
                ).fetchall()
        return [dict(r) for r in rows]

    def clear_relationships(self, original_file: str | None = None) -> None:
        """清除关系（可选按文件）。"""
        with self._lock:
            try:
                if original_file:
                    self._conn.execute(
                        "DELETE FROM relationships WHERE original_file = ?",
                        (original_file,))
                else:
                    self._conn.execute("DELETE FROM relationships")
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise
