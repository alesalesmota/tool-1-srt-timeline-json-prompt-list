from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

from .config import DATABASE_PATH, DEFAULT_SETTINGS
from .runtime import ensure_dir, utc_now


class Tool1Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DATABASE_PATH
        ensure_dir(self.path.parent)
        self._lock = threading.Lock()

    def initialize(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS templates (
                stage TEXT NOT NULL,
                provider TEXT NOT NULL,
                path TEXT NOT NULL,
                body TEXT NOT NULL,
                hash TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (stage, provider)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                board_status TEXT NOT NULL,
                pipeline_status TEXT NOT NULL,
                current_stage TEXT NOT NULL,
                queued_from_stage TEXT NOT NULL,
                language_code TEXT NOT NULL,
                scene_provider TEXT NOT NULL,
                prompt_provider TEXT NOT NULL,
                scene_planning_provider TEXT NOT NULL DEFAULT 'claude',
                visual_bible_provider TEXT NOT NULL DEFAULT 'claude',
                video_prompt_provider TEXT NOT NULL DEFAULT 'codex',
                image_prompt_provider TEXT NOT NULL DEFAULT 'codex',
                leading_video_scene_count INTEGER NOT NULL DEFAULT 20,
                workspace_dir TEXT NOT NULL,
                audio_filename TEXT NOT NULL,
                script_filename TEXT NOT NULL,
                audio_path TEXT NOT NULL,
                script_path TEXT NOT NULL,
                final_srt_path TEXT,
                alignment_report_path TEXT,
                segments_path TEXT,
                planning_manifest_path TEXT,
                timeline_draft_path TEXT,
                timeline_validation_path TEXT,
                visual_bible_path TEXT,
                visual_bible_validation_path TEXT,
                prompt_list_draft_path TEXT,
                prompt_blueprint_path TEXT,
                prompt_validation_path TEXT,
                export_timeline_path TEXT,
                export_prompt_list_path TEXT,
                export_video_prompt_list_path TEXT,
                export_image_prompt_list_path TEXT,
                review_ready INTEGER NOT NULL DEFAULT 0,
                warning_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS stage_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                provider TEXT,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                exit_code INTEGER,
                template_hash TEXT,
                workdir TEXT,
                command_json TEXT,
                stdout_path TEXT,
                stderr_path TEXT,
                parsed_output_path TEXT,
                validation_path TEXT,
                error_text TEXT
            )
            """,
        ]
        with self._connect() as connection:
            for statement in statements:
                connection.execute(statement)
            self._ensure_columns(
                connection,
                "jobs",
                {
                    "scene_planning_provider": "TEXT NOT NULL DEFAULT 'claude'",
                    "visual_bible_provider": "TEXT NOT NULL DEFAULT 'claude'",
                    "video_prompt_provider": "TEXT NOT NULL DEFAULT 'codex'",
                    "image_prompt_provider": "TEXT NOT NULL DEFAULT 'codex'",
                    "leading_video_scene_count": "INTEGER NOT NULL DEFAULT 20",
                    "visual_bible_path": "TEXT",
                    "visual_bible_validation_path": "TEXT",
                    "prompt_blueprint_path": "TEXT",
                    "export_video_prompt_list_path": "TEXT",
                    "export_image_prompt_list_path": "TEXT",
                },
            )
            connection.execute(
                """
                UPDATE jobs
                SET scene_planning_provider = COALESCE(NULLIF(scene_planning_provider, ''), scene_provider, 'claude'),
                    visual_bible_provider = COALESCE(NULLIF(visual_bible_provider, ''), prompt_provider, 'claude'),
                    video_prompt_provider = COALESCE(NULLIF(video_prompt_provider, ''), prompt_provider, 'codex'),
                    image_prompt_provider = COALESCE(NULLIF(image_prompt_provider, ''), prompt_provider, 'codex'),
                    leading_video_scene_count = COALESCE(leading_video_scene_count, 20)
                """
            )
            connection.commit()
        for key, value in DEFAULT_SETTINGS.items():
            self.set_setting(key, value)

    @staticmethod
    def _ensure_columns(
        connection: sqlite3.Connection,
        table_name: str,
        columns: dict[str, str],
    ) -> None:
        existing = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        for column_name, column_def in columns.items():
            if column_name in existing:
                continue
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}"
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def _fetchall(self, query: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._row_to_dict(row) or {} for row in rows]

    def _fetchone(self, query: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return self._row_to_dict(row)

    def _execute(self, query: str, params: Iterable[Any] = ()) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(query, tuple(params))
            connection.commit()
            return int(cursor.lastrowid or 0)

    def set_setting(self, key: str, value: Any) -> None:
        payload = json.dumps(value)
        self._execute(
            """
            INSERT INTO settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, payload, utc_now()),
        )

    def get_settings(self) -> dict[str, Any]:
        rows = self._fetchall("SELECT key, value FROM settings ORDER BY key")
        payload: dict[str, Any] = {}
        for row in rows:
            try:
                payload[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                payload[row["key"]] = row["value"]
        return payload

    def upsert_template(self, stage: str, provider: str, path: str, body: str, template_hash: str) -> None:
        self._execute(
            """
            INSERT INTO templates(stage, provider, path, body, hash, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(stage, provider) DO UPDATE SET
                path = excluded.path,
                body = excluded.body,
                hash = excluded.hash,
                updated_at = excluded.updated_at
            """,
            (stage, provider, path, body, template_hash, utc_now()),
        )

    def list_templates(self) -> list[dict[str, Any]]:
        return self._fetchall("SELECT * FROM templates ORDER BY stage, provider")

    def create_job(self, payload: dict[str, Any]) -> None:
        columns = ", ".join(payload.keys())
        placeholders = ", ".join(["?"] * len(payload))
        self._execute(
            f"INSERT INTO jobs({columns}) VALUES ({placeholders})",
            tuple(payload.values()),
        )

    def list_jobs(self) -> list[dict[str, Any]]:
        return self._fetchall("SELECT * FROM jobs ORDER BY updated_at DESC, created_at DESC")

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [job_id]
        self._execute(f"UPDATE jobs SET {assignments} WHERE id = ?", params)

    def list_stage_runs(self, job_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM stage_runs WHERE job_id = ? ORDER BY id DESC",
            (job_id,),
        )

    def start_stage_run(
        self,
        job_id: str,
        stage: str,
        provider: str | None,
        template_hash: str | None,
        workdir: str,
        command_payload: Any,
        stdout_path: str | None,
        stderr_path: str | None,
        parsed_output_path: str | None = None,
        validation_path: str | None = None,
    ) -> int:
        return self._execute(
            """
            INSERT INTO stage_runs(
                job_id, stage, provider, status, started_at, template_hash, workdir,
                command_json, stdout_path, stderr_path, parsed_output_path, validation_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                stage,
                provider,
                "running",
                utc_now(),
                template_hash,
                workdir,
                json.dumps(command_payload, ensure_ascii=False),
                stdout_path,
                stderr_path,
                parsed_output_path,
                validation_path,
            ),
        )

    def finish_stage_run(
        self,
        run_id: int,
        *,
        status: str,
        exit_code: int | None,
        parsed_output_path: str | None = None,
        validation_path: str | None = None,
        error_text: str | None = None,
        command_payload: Any | None = None,
        stdout_path: str | None = None,
        stderr_path: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "status": status,
            "finished_at": utc_now(),
            "exit_code": exit_code,
            "parsed_output_path": parsed_output_path,
            "validation_path": validation_path,
            "error_text": error_text,
        }
        if command_payload is not None:
            fields["command_json"] = json.dumps(command_payload, ensure_ascii=False)
        if stdout_path is not None:
            fields["stdout_path"] = stdout_path
        if stderr_path is not None:
            fields["stderr_path"] = stderr_path
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [run_id]
        self._execute(f"UPDATE stage_runs SET {assignments} WHERE id = ?", params)

    def next_queued_job(self) -> dict[str, Any] | None:
        return self._fetchone(
            """
            SELECT * FROM jobs
            WHERE board_status = 'Queued'
            ORDER BY updated_at ASC, created_at ASC
            LIMIT 1
            """
        )
