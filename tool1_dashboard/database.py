from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from .config import DATABASE_PATH, DEFAULT_SETTINGS, LEGACY_DATABASE_PATH
from .runtime import ensure_dir, utc_now


class Tool1Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DATABASE_PATH
        ensure_dir(self.path.parent)
        self._lock = threading.Lock()

    # ── initialisation ──────────────────────────────────────────────

    def initialize(self) -> None:
        self._maybe_migrate_legacy_db()
        self._migrate_videos_to_episodes()
        self._create_tables()
        self._migrate_jobs_to_projects()
        for key, value in DEFAULT_SETTINGS.items():
            self.set_setting(key, value)

    def _maybe_migrate_legacy_db(self) -> None:
        """Copy legacy tool1_dashboard.db → creator_studio.db if the new DB doesn't exist yet."""
        if self.path.exists():
            return
        if LEGACY_DATABASE_PATH.exists():
            shutil.copy2(LEGACY_DATABASE_PATH, self.path)

    def _migrate_videos_to_episodes(self) -> None:
        """Rename videos → episodes and video_language_status → episode_language_status."""
        if not self.path.exists():
            return
        with self._lock, self._connect() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
            if "videos" in tables and "episodes" not in tables:
                conn.execute("ALTER TABLE videos RENAME TO episodes")
            if "video_language_status" in tables and "episode_language_status" not in tables:
                # Rebuild table to rename video_id → episode_id
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS episode_language_status (
                        id TEXT PRIMARY KEY,
                        episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                        language_code TEXT NOT NULL,
                        translation_status TEXT NOT NULL DEFAULT 'pending',
                        tts_status TEXT NOT NULL DEFAULT 'pending',
                        srt_status TEXT NOT NULL DEFAULT 'pending',
                        timeline_status TEXT NOT NULL DEFAULT 'pending',
                        script_path TEXT,
                        tts_audio_path TEXT,
                        srt_path TEXT,
                        timeline_path TEXT,
                        tts_job_id TEXT,
                        error_message TEXT,
                        updated_at TEXT NOT NULL,
                        UNIQUE(episode_id, language_code)
                    )
                """)
                conn.execute("""
                    INSERT INTO episode_language_status
                    SELECT id, video_id, language_code, translation_status, tts_status,
                           srt_status, timeline_status, script_path, tts_audio_path,
                           srt_path, timeline_path, tts_job_id, error_message, updated_at
                    FROM video_language_status
                """)
                conn.execute("DROP TABLE video_language_status")
            conn.commit()

    def _create_tables(self) -> None:
        statements = [
            # ── existing tables ──
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
                scene_planning_model TEXT NOT NULL DEFAULT 'haiku',
                visual_bible_model TEXT NOT NULL DEFAULT 'haiku',
                video_prompt_model TEXT NOT NULL DEFAULT 'gpt-5.4',
                image_prompt_model TEXT NOT NULL DEFAULT 'gpt-5.4',
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
            # ── new tables (Creator Studio multilingual) ──
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_language TEXT NOT NULL DEFAULT 'en',
                board_status TEXT NOT NULL DEFAULT 'Draft',
                workspace_dir TEXT NOT NULL,
                audio_filename TEXT,
                script_filename TEXT,
                audio_path TEXT,
                script_path TEXT,
                master_scenes_path TEXT,
                consistency_guide_path TEXT,
                asset_manifest_path TEXT,
                master_prompt_list_path TEXT,
                master_video_prompt_list_path TEXT,
                master_image_prompt_list_path TEXT,
                scene_planning_provider TEXT NOT NULL DEFAULT 'claude',
                visual_bible_provider TEXT NOT NULL DEFAULT 'claude',
                video_prompt_provider TEXT NOT NULL DEFAULT 'codex',
                image_prompt_provider TEXT NOT NULL DEFAULT 'codex',
                scene_planning_model TEXT NOT NULL DEFAULT 'haiku',
                visual_bible_model TEXT NOT NULL DEFAULT 'haiku',
                video_prompt_model TEXT NOT NULL DEFAULT 'gpt-5.4',
                image_prompt_model TEXT NOT NULL DEFAULT 'gpt-5.4',
                leading_video_scene_count INTEGER NOT NULL DEFAULT 20,
                warning_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS builds (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id),
                build_type TEXT NOT NULL DEFAULT 'master',
                language_code TEXT NOT NULL,
                board_status TEXT NOT NULL DEFAULT 'Draft',
                pipeline_status TEXT NOT NULL DEFAULT 'idle',
                current_stage TEXT NOT NULL DEFAULT 'draft',
                queued_from_stage TEXT NOT NULL DEFAULT 'alignment',
                script_path TEXT,
                audio_path TEXT,
                srt_path TEXT,
                timeline_path TEXT,
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
                translation_draft_path TEXT,
                translation_chunks_path TEXT,
                tts_job_id TEXT,
                narration_path TEXT,
                review_ready INTEGER NOT NULL DEFAULT 0,
                warning_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                workspace_dir TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS voice_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                language_code TEXT NOT NULL DEFAULT '',
                audio_file TEXT NOT NULL,
                audio_path TEXT NOT NULL,
                latents_path TEXT,
                has_latents INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS translation_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                provider TEXT NOT NULL,
                api_key_ref TEXT NOT NULL DEFAULT '',
                model TEXT NOT NULL,
                is_default INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS tts_jobs (
                job_id TEXT PRIMARY KEY,
                build_id TEXT,
                job_type TEXT NOT NULL DEFAULT 'generate',
                profile_id TEXT,
                status TEXT NOT NULL DEFAULT 'queued',
                progress TEXT,
                result_path TEXT,
                filename TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                meta_json TEXT,
                queue_priority INTEGER NOT NULL DEFAULT 10,
                worker_id TEXT,
                control_action TEXT,
                error_message TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                finished_at REAL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_builds_project
            ON builds(project_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_tts_jobs_queue
            ON tts_jobs(status, queue_priority, created_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_tts_jobs_build
            ON tts_jobs(build_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS worker_heartbeats (
                worker_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                current_job_id TEXT,
                pid INTEGER,
                started_at REAL NOT NULL,
                heartbeat_at REAL NOT NULL,
                last_error TEXT
            )
            """,
            # ── Episode pipeline (TTS-first unified) ──
            """
            CREATE TABLE IF NOT EXISTS episodes (
                id TEXT PRIMARY KEY,
                niche_project_id TEXT NOT NULL REFERENCES projects(id),
                title TEXT NOT NULL,
                script_text TEXT NOT NULL,
                board_status TEXT NOT NULL DEFAULT 'Draft',
                pipeline_status TEXT NOT NULL DEFAULT 'idle',
                current_stage TEXT NOT NULL DEFAULT 'draft',
                queued_from_stage TEXT NOT NULL DEFAULT 'consistency_guide',
                master_language TEXT NOT NULL DEFAULT 'en',
                configured_languages TEXT NOT NULL DEFAULT '[]',
                consistency_guide_path TEXT,
                planning_manifest_path TEXT,
                timeline_draft_path TEXT,
                timeline_validation_path TEXT,
                visual_bible_validation_path TEXT,
                prompt_list_draft_path TEXT,
                prompt_blueprint_path TEXT,
                prompt_validation_path TEXT,
                export_timeline_path TEXT,
                export_prompt_list_path TEXT,
                export_video_prompt_list_path TEXT,
                export_image_prompt_list_path TEXT,
                master_scenes_path TEXT,
                review_ready INTEGER NOT NULL DEFAULT 0,
                warning_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                workspace_dir TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS episode_language_status (
                id TEXT PRIMARY KEY,
                episode_id TEXT NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
                language_code TEXT NOT NULL,
                translation_status TEXT NOT NULL DEFAULT 'pending',
                tts_status TEXT NOT NULL DEFAULT 'pending',
                srt_status TEXT NOT NULL DEFAULT 'pending',
                timeline_status TEXT NOT NULL DEFAULT 'pending',
                script_path TEXT,
                tts_audio_path TEXT,
                srt_path TEXT,
                timeline_path TEXT,
                tts_job_id TEXT,
                error_message TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(episode_id, language_code)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_episodes_niche
            ON episodes(niche_project_id)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_els_episode
            ON episode_language_status(episode_id)
            """,
        ]
        with self._connect() as connection:
            for statement in statements:
                connection.execute(statement)
            # ensure columns on legacy jobs table
            self._ensure_columns(
                connection,
                "jobs",
                {
                    "scene_planning_provider": "TEXT NOT NULL DEFAULT 'claude'",
                    "visual_bible_provider": "TEXT NOT NULL DEFAULT 'claude'",
                    "video_prompt_provider": "TEXT NOT NULL DEFAULT 'codex'",
                    "image_prompt_provider": "TEXT NOT NULL DEFAULT 'codex'",
                    "scene_planning_model": "TEXT NOT NULL DEFAULT 'haiku'",
                    "visual_bible_model": "TEXT NOT NULL DEFAULT 'haiku'",
                    "video_prompt_model": "TEXT NOT NULL DEFAULT 'gpt-5.4'",
                    "image_prompt_model": "TEXT NOT NULL DEFAULT 'gpt-5.4'",
                    "leading_video_scene_count": "INTEGER NOT NULL DEFAULT 20",
                    "visual_bible_path": "TEXT",
                    "visual_bible_validation_path": "TEXT",
                    "prompt_blueprint_path": "TEXT",
                    "export_video_prompt_list_path": "TEXT",
                    "export_image_prompt_list_path": "TEXT",
                    "project_id": "TEXT",
                },
            )
            # ensure build_id column on stage_runs
            self._ensure_columns(
                connection,
                "stage_runs",
                {"build_id": "TEXT"},
            )
            # ensure profile columns on builds
            self._ensure_columns(
                connection,
                "builds",
                {
                    "translation_profile_id": "TEXT",
                    "voice_profile_id": "TEXT",
                },
            )
            # ensure niche project columns on projects
            self._ensure_columns(
                connection,
                "projects",
                {
                    "master_language": "TEXT DEFAULT 'en'",
                    "configured_languages": "TEXT DEFAULT '[]'",
                    "language_voice_profiles": "TEXT DEFAULT '{}'",
                    "language_translation_profiles": "TEXT DEFAULT '{}'",
                    "is_niche": "INTEGER DEFAULT 0",
                },
            )
            connection.execute(
                """
                UPDATE jobs
                SET scene_planning_provider = COALESCE(NULLIF(scene_planning_provider, ''), scene_provider, 'claude'),
                    visual_bible_provider = COALESCE(NULLIF(visual_bible_provider, ''), prompt_provider, 'claude'),
                    video_prompt_provider = COALESCE(NULLIF(video_prompt_provider, ''), prompt_provider, 'codex'),
                    image_prompt_provider = COALESCE(NULLIF(image_prompt_provider, ''), prompt_provider, 'codex'),
                    scene_planning_model = COALESCE(NULLIF(scene_planning_model, ''), 'haiku'),
                    visual_bible_model = COALESCE(NULLIF(visual_bible_model, ''), 'haiku'),
                    video_prompt_model = COALESCE(NULLIF(video_prompt_model, ''), 'gpt-5.4'),
                    image_prompt_model = COALESCE(NULLIF(image_prompt_model, ''), 'gpt-5.4'),
                    leading_video_scene_count = COALESCE(leading_video_scene_count, 20)
                """
            )
            connection.commit()

    def _migrate_jobs_to_projects(self) -> None:
        """For each legacy job that has no project_id, create a project + master build."""
        with self._lock, self._connect() as connection:
            orphan_jobs = connection.execute(
                "SELECT * FROM jobs WHERE project_id IS NULL OR project_id = ''"
            ).fetchall()
            if not orphan_jobs:
                return
            now = utc_now()
            for job_row in orphan_jobs:
                job = dict(job_row)
                job_id = job["id"]
                project_id = f"proj-{job_id}"
                build_id = f"build-master-{job_id}"

                # Create project
                connection.execute(
                    """
                    INSERT OR IGNORE INTO projects(
                        id, title, source_language, board_status, workspace_dir,
                        audio_filename, script_filename, audio_path, script_path,
                        scene_planning_provider, visual_bible_provider,
                        video_prompt_provider, image_prompt_provider,
                        scene_planning_model, visual_bible_model,
                        video_prompt_model, image_prompt_model,
                        leading_video_scene_count, warning_count, last_error,
                        created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        project_id,
                        job.get("title", job_id),
                        job.get("language_code", "en"),
                        job.get("board_status", "Draft"),
                        job.get("workspace_dir", ""),
                        job.get("audio_filename"),
                        job.get("script_filename"),
                        job.get("audio_path"),
                        job.get("script_path"),
                        job.get("scene_planning_provider", "claude"),
                        job.get("visual_bible_provider", "claude"),
                        job.get("video_prompt_provider", "codex"),
                        job.get("image_prompt_provider", "codex"),
                        job.get("scene_planning_model", "haiku"),
                        job.get("visual_bible_model", "haiku"),
                        job.get("video_prompt_model", "gpt-5.4"),
                        job.get("image_prompt_model", "gpt-5.4"),
                        job.get("leading_video_scene_count", 20),
                        job.get("warning_count", 0),
                        job.get("last_error"),
                        job.get("created_at", now),
                        now,
                    ),
                )

                # Create master build
                connection.execute(
                    """
                    INSERT OR IGNORE INTO builds(
                        id, project_id, build_type, language_code,
                        board_status, pipeline_status, current_stage, queued_from_stage,
                        script_path, audio_path, srt_path, timeline_path,
                        alignment_report_path, segments_path,
                        planning_manifest_path, timeline_draft_path, timeline_validation_path,
                        visual_bible_path, visual_bible_validation_path,
                        prompt_list_draft_path, prompt_blueprint_path, prompt_validation_path,
                        export_timeline_path, export_prompt_list_path,
                        export_video_prompt_list_path, export_image_prompt_list_path,
                        review_ready, warning_count, last_error,
                        workspace_dir, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        build_id,
                        project_id,
                        "master",
                        job.get("language_code", "en"),
                        job.get("board_status", "Draft"),
                        job.get("pipeline_status", "idle"),
                        job.get("current_stage", "draft"),
                        job.get("queued_from_stage", "alignment"),
                        job.get("script_path"),
                        job.get("audio_path"),
                        job.get("final_srt_path"),
                        job.get("timeline_draft_path"),
                        job.get("alignment_report_path"),
                        job.get("segments_path"),
                        job.get("planning_manifest_path"),
                        job.get("timeline_draft_path"),
                        job.get("timeline_validation_path"),
                        job.get("visual_bible_path"),
                        job.get("visual_bible_validation_path"),
                        job.get("prompt_list_draft_path"),
                        job.get("prompt_blueprint_path"),
                        job.get("prompt_validation_path"),
                        job.get("export_timeline_path"),
                        job.get("export_prompt_list_path"),
                        job.get("export_video_prompt_list_path"),
                        job.get("export_image_prompt_list_path"),
                        job.get("review_ready", 0),
                        job.get("warning_count", 0),
                        job.get("last_error"),
                        job.get("workspace_dir", ""),
                        job.get("created_at", now),
                        now,
                    ),
                )

                # Link job to project
                connection.execute(
                    "UPDATE jobs SET project_id = ? WHERE id = ?",
                    (project_id, job_id),
                )

                # Backfill build_id on stage_runs
                connection.execute(
                    "UPDATE stage_runs SET build_id = ? WHERE job_id = ? AND (build_id IS NULL OR build_id = '')",
                    (build_id, job_id),
                )

            connection.commit()

    # ── helpers ──────────────────────────────────────────────────────

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
        connection.execute("PRAGMA journal_mode=WAL")
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

    def _insert(self, table: str, payload: dict[str, Any]) -> None:
        columns = ", ".join(payload.keys())
        placeholders = ", ".join(["?"] * len(payload))
        self._execute(
            f"INSERT INTO {table}({columns}) VALUES ({placeholders})",
            tuple(payload.values()),
        )

    def _update(self, table: str, pk_column: str, pk_value: Any, **fields: Any) -> None:
        if not fields:
            return
        if "updated_at" not in fields:
            fields["updated_at"] = utc_now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        params = list(fields.values()) + [pk_value]
        self._execute(f"UPDATE {table} SET {assignments} WHERE {pk_column} = ?", params)

    # ── settings ────────────────────────────────────────────────────

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

    # ── templates ───────────────────────────────────────────────────

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

    # ── legacy jobs (backward compat) ───────────────────────────────

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

    def has_running_stage_run(self, job_id: str) -> bool:
        row = self._fetchone(
            """
            SELECT id FROM stage_runs
            WHERE job_id = ? AND status = 'running'
            LIMIT 1
            """,
            (job_id,),
        )
        return row is not None

    def delete_job_records(self, job_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM stage_runs WHERE job_id = ?", (job_id,))
            connection.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            connection.commit()

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

    def update_stage_run_command(self, run_id: int, command_payload: Any) -> None:
        self._execute(
            "UPDATE stage_runs SET command_json = ? WHERE id = ?",
            (json.dumps(command_payload, ensure_ascii=False), run_id),
        )

    def next_queued_job(self) -> dict[str, Any] | None:
        return self._fetchone(
            """
            SELECT * FROM jobs
            WHERE board_status = 'Queued'
            ORDER BY updated_at ASC, created_at ASC
            LIMIT 1
            """
        )

    # ── projects ────────────────────────────────────────────────────

    def create_project(self, payload: dict[str, Any]) -> None:
        self._insert("projects", payload)

    def list_projects(self) -> list[dict[str, Any]]:
        return self._fetchall("SELECT * FROM projects ORDER BY updated_at DESC, created_at DESC")

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        return self._fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))

    def update_project(self, project_id: str, **fields: Any) -> None:
        self._update("projects", "id", project_id, **fields)

    def delete_project(self, project_id: str) -> None:
        with self._lock, self._connect() as connection:
            build_ids = [
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM builds WHERE project_id = ?", (project_id,)
                ).fetchall()
            ]
            for build_id in build_ids:
                connection.execute("DELETE FROM stage_runs WHERE build_id = ?", (build_id,))
                connection.execute("DELETE FROM tts_jobs WHERE build_id = ?", (build_id,))
            connection.execute("DELETE FROM builds WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            connection.commit()

    # ── builds ──────────────────────────────────────────────────────

    def create_build(self, payload: dict[str, Any]) -> None:
        self._insert("builds", payload)

    def list_builds(self, project_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM builds WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        )

    def get_build(self, build_id: str) -> dict[str, Any] | None:
        return self._fetchone("SELECT * FROM builds WHERE id = ?", (build_id,))

    def get_master_build(self, project_id: str) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT * FROM builds WHERE project_id = ? AND build_type = 'master' LIMIT 1",
            (project_id,),
        )

    def list_localization_builds(self, project_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM builds WHERE project_id = ? AND build_type = 'localization' ORDER BY language_code",
            (project_id,),
        )

    def update_build(self, build_id: str, **fields: Any) -> None:
        self._update("builds", "id", build_id, **fields)

    def delete_build(self, build_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM stage_runs WHERE build_id = ?", (build_id,))
            connection.execute("DELETE FROM tts_jobs WHERE build_id = ?", (build_id,))
            connection.execute("DELETE FROM builds WHERE id = ?", (build_id,))
            connection.commit()

    def next_queued_build(self) -> dict[str, Any] | None:
        return self._fetchone(
            """
            SELECT * FROM builds
            WHERE board_status = 'Queued'
            ORDER BY updated_at ASC, created_at ASC
            LIMIT 1
            """
        )

    def list_build_stage_runs(self, build_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM stage_runs WHERE build_id = ? ORDER BY id DESC",
            (build_id,),
        )

    def start_build_stage_run(
        self,
        build_id: str,
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
                build_id, job_id, stage, provider, status, started_at, template_hash, workdir,
                command_json, stdout_path, stderr_path, parsed_output_path, validation_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                build_id,
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

    # ── voice profiles ──────────────────────────────────────────────

    def create_voice_profile(self, payload: dict[str, Any]) -> None:
        self._insert("voice_profiles", payload)

    def list_voice_profiles(self) -> list[dict[str, Any]]:
        return self._fetchall("SELECT * FROM voice_profiles ORDER BY name")

    def get_voice_profile(self, profile_id: str) -> dict[str, Any] | None:
        return self._fetchone("SELECT * FROM voice_profiles WHERE id = ?", (profile_id,))

    def update_voice_profile(self, profile_id: str, **fields: Any) -> None:
        self._update("voice_profiles", "id", profile_id, **fields)

    def delete_voice_profile(self, profile_id: str) -> None:
        self._execute("DELETE FROM voice_profiles WHERE id = ?", (profile_id,))

    # ── translation profiles ────────────────────────────────────────

    def create_translation_profile(self, payload: dict[str, Any]) -> None:
        self._insert("translation_profiles", payload)

    def list_translation_profiles(self) -> list[dict[str, Any]]:
        return self._fetchall("SELECT * FROM translation_profiles ORDER BY name")

    def get_translation_profile(self, profile_id: str) -> dict[str, Any] | None:
        return self._fetchone("SELECT * FROM translation_profiles WHERE id = ?", (profile_id,))

    def update_translation_profile(self, profile_id: str, **fields: Any) -> None:
        self._update("translation_profiles", "id", profile_id, **fields)

    def delete_translation_profile(self, profile_id: str) -> None:
        self._execute("DELETE FROM translation_profiles WHERE id = ?", (profile_id,))

    # ── TTS jobs ────────────────────────────────────────────────────

    def create_tts_job(self, payload: dict[str, Any]) -> None:
        self._insert("tts_jobs", payload)

    def get_tts_job(self, job_id: str) -> dict[str, Any] | None:
        return self._fetchone("SELECT * FROM tts_jobs WHERE job_id = ?", (job_id,))

    def update_tts_job(self, job_id: str, **fields: Any) -> None:
        self._update("tts_jobs", "job_id", job_id, **fields)

    def next_queued_tts_job(self) -> dict[str, Any] | None:
        return self._fetchone(
            """
            SELECT * FROM tts_jobs
            WHERE status = 'queued'
            ORDER BY queue_priority ASC, created_at ASC
            LIMIT 1
            """
        )

    def list_tts_jobs_for_build(self, build_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM tts_jobs WHERE build_id = ? ORDER BY created_at DESC",
            (build_id,),
        )

    def list_active_tts_jobs(self) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM tts_jobs WHERE status IN ('queued', 'processing') ORDER BY queue_priority ASC, created_at ASC"
        )

    def list_recent_tts_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM tts_jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )

    # ── worker heartbeats ────────────────────────────────────────────

    def record_worker_heartbeat(
        self,
        *,
        worker_id: str,
        status: str,
        current_job_id: str | None,
        pid: int,
        started_at: float,
        last_error: str | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO worker_heartbeats (
                    worker_id, status, current_job_id, pid,
                    started_at, heartbeat_at, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    status = excluded.status,
                    current_job_id = excluded.current_job_id,
                    pid = excluded.pid,
                    started_at = excluded.started_at,
                    heartbeat_at = excluded.heartbeat_at,
                    last_error = excluded.last_error
                """,
                (worker_id, status, current_job_id, pid, started_at, time.time(), last_error),
            )

    def get_latest_worker_heartbeat(self) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT * FROM worker_heartbeats ORDER BY heartbeat_at DESC LIMIT 1"
        )

    def claim_next_tts_job(self, worker_id: str) -> dict[str, Any] | None:
        """Atomically claim the next queued TTS job for *worker_id*.

        Uses BEGIN IMMEDIATE for cross-process safety with WAL mode.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT * FROM tts_jobs
                WHERE status = 'queued'
                ORDER BY queue_priority ASC, created_at ASC
                LIMIT 1
                """
            ).fetchone()

            if row is None:
                conn.execute("COMMIT")
                return None

            job_id = row["job_id"]
            now = time.time()
            updated = conn.execute(
                """
                UPDATE tts_jobs
                SET status = 'processing',
                    updated_at = ?,
                    worker_id = ?,
                    progress = CASE
                        WHEN progress IS NULL OR progress = '' THEN 'Processing...'
                        ELSE progress
                    END
                WHERE job_id = ?
                AND status = 'queued'
                """,
                (now, worker_id, job_id),
            ).rowcount

            if updated != 1:
                conn.execute("ROLLBACK")
                return None

            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

        return self.get_tts_job(job_id)

    def requeue_stale_tts_jobs(self, stale_seconds: int) -> int:
        """Reset TTS jobs stuck in 'processing' beyond *stale_seconds* to 'queued'."""
        cutoff = time.time() - stale_seconds
        now = time.time()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE tts_jobs
                SET status = 'queued',
                    progress = 'Requeued after worker restart.',
                    worker_id = NULL,
                    control_action = NULL,
                    updated_at = ?
                WHERE status = 'processing'
                AND updated_at < ?
                """,
                (now, cutoff),
            )
            return cursor.rowcount

    def get_latest_latent_job_for_profile(self, profile_id: str) -> dict[str, Any] | None:
        return self._fetchone(
            """
            SELECT * FROM tts_jobs
            WHERE job_type = 'latent_precompute'
            AND profile_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (profile_id,),
        )

    def list_paused_tts_builds(self) -> list[dict[str, Any]]:
        """Return localization builds currently paused waiting for TTS."""
        return self._fetchall(
            "SELECT * FROM builds WHERE pipeline_status = 'paused_for_tts' AND tts_job_id IS NOT NULL"
        )

    # ── Episodes (TTS-first unified pipeline) ────────────────────────

    def create_episode(self, payload: dict[str, Any]) -> None:
        self._insert("episodes", payload)

    def list_episodes(self, niche_project_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM episodes WHERE niche_project_id = ? ORDER BY created_at DESC",
            (niche_project_id,),
        )

    def get_episode(self, episode_id: str) -> dict[str, Any] | None:
        return self._fetchone("SELECT * FROM episodes WHERE id = ?", (episode_id,))

    def update_episode(self, episode_id: str, **fields: Any) -> None:
        self._update("episodes", "id", episode_id, **fields)

    def delete_episode(self, episode_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM episode_language_status WHERE episode_id = ?", (episode_id,))
            connection.execute("DELETE FROM stage_runs WHERE build_id = ?", (episode_id,))
            connection.execute("DELETE FROM tts_jobs WHERE build_id = ?", (episode_id,))
            connection.execute("DELETE FROM episodes WHERE id = ?", (episode_id,))
            connection.commit()

    def next_queued_episode(self) -> dict[str, Any] | None:
        return self._fetchone(
            """
            SELECT * FROM episodes
            WHERE board_status = 'Queued'
            ORDER BY updated_at ASC, created_at ASC
            LIMIT 1
            """
        )

    def list_all_episodes_for_board(self) -> list[dict[str, Any]]:
        """Return all episodes with their niche project title for the kanban board."""
        return self._fetchall(
            """
            SELECT e.*, p.title as niche_project_title
            FROM episodes e
            JOIN projects p ON e.niche_project_id = p.id
            ORDER BY e.updated_at DESC
            """
        )

    # ── Episode language status ──────────────────────────────────────

    def create_episode_language_status(self, payload: dict[str, Any]) -> None:
        self._insert("episode_language_status", payload)

    def get_episode_language_statuses(self, episode_id: str) -> list[dict[str, Any]]:
        return self._fetchall(
            "SELECT * FROM episode_language_status WHERE episode_id = ? ORDER BY language_code",
            (episode_id,),
        )

    def get_episode_language_status(self, episode_id: str, language_code: str) -> dict[str, Any] | None:
        return self._fetchone(
            "SELECT * FROM episode_language_status WHERE episode_id = ? AND language_code = ?",
            (episode_id, language_code),
        )

    def update_episode_language_status(self, episode_id: str, language_code: str, **fields: Any) -> None:
        fields["updated_at"] = utc_now()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [episode_id, language_code]
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE episode_language_status SET {set_clause} WHERE episode_id = ? AND language_code = ?",
                values,
            )
            connection.commit()

    def list_paused_tts_episodes(self) -> list[dict[str, Any]]:
        """Return episodes currently paused waiting for TTS to complete."""
        return self._fetchall(
            "SELECT * FROM episodes WHERE pipeline_status = 'paused_for_tts'"
        )

    def list_niche_projects(self) -> list[dict[str, Any]]:
        """Return projects that are niche projects (have is_niche=1)."""
        return self._fetchall(
            "SELECT * FROM projects WHERE is_niche = 1 ORDER BY updated_at DESC"
        )
