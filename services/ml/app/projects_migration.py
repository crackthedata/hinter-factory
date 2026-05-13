# See docs/notes-ml.md#servicesmlappprojects_migrationpy for the full rationale.

from __future__ import annotations

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

_TABLES_NEEDING_PROJECT_ID = (
    "documents",
    "tags",
    "labeling_functions",
    "lf_runs",
    "gold_labels",
    "probabilistic_labels",
)


def migrate(engine: Engine) -> None:
    inspector = inspect(engine)
    with engine.begin() as conn:
        if "projects" not in inspector.get_table_names():
            return
        _add_project_id_columns(conn, inspector)
        _migrate_tags_unique_constraint(conn, inspector)
        _add_probabilistic_label_vote_columns(conn, inspector)
        _warn_on_orphan_rows(conn)


def _add_project_id_columns(conn, inspector) -> None:
    existing_tables = set(inspector.get_table_names())
    for table_name in _TABLES_NEEDING_PROJECT_ID:
        if table_name not in existing_tables:
            continue
        columns = {c["name"] for c in inspector.get_columns(table_name)}
        if "project_id" in columns:
            continue
        conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN project_id TEXT"))


def _migrate_tags_unique_constraint(conn, inspector) -> None:
    if "tags" not in inspector.get_table_names():
        return
    create_sql_row = conn.execute(
        text("SELECT sql FROM sqlite_master WHERE type='table' AND name='tags'")
    ).first()
    if not create_sql_row:
        return
    create_sql = (create_sql_row[0] or "").lower()
    has_old_unique = "unique" in create_sql and "uq_tags_project_name" not in create_sql and (
        "name varchar" in create_sql.replace("\n", " ") and ("unique" in create_sql.split("name varchar", 1)[1][:200])
        or "name)" in create_sql and "unique (name)" in create_sql.replace('"', "").replace("`", "")
    )
    has_composite = "uq_tags_project_name" in create_sql
    if has_composite:
        return

    idx_rows = conn.execute(text("PRAGMA index_list(tags)")).all()
    needs_rebuild = False
    for idx in idx_rows:
        if int(idx[2]) != 1:
            continue
        idx_name = idx[1]
        info = conn.execute(text(f"PRAGMA index_info({idx_name})")).all()
        cols = [r[2] for r in info]
        if cols == ["name"]:
            needs_rebuild = True
            break
    if not needs_rebuild and not has_old_unique:
        return

    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        conn.execute(
            text(
                """
                CREATE TABLE tags_new (
                    id VARCHAR(36) NOT NULL PRIMARY KEY,
                    project_id VARCHAR(36) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    taxonomy_version VARCHAR(64) NOT NULL,
                    created_at DATETIME NOT NULL,
                    CONSTRAINT uq_tags_project_name UNIQUE (project_id, name),
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO tags_new (id, project_id, name, taxonomy_version, created_at)
                SELECT id, project_id, name, taxonomy_version, created_at FROM tags
                """
            )
        )
        conn.execute(text("DROP TABLE tags"))
        conn.execute(text("ALTER TABLE tags_new RENAME TO tags"))
        conn.execute(text("CREATE INDEX ix_tags_project_id ON tags(project_id)"))
    finally:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def _add_probabilistic_label_vote_columns(conn, inspector) -> None:
    if "probabilistic_labels" not in inspector.get_table_names():
        return
    columns = {c["name"] for c in inspector.get_columns("probabilistic_labels")}
    if "positive_votes" not in columns:
        conn.execute(text("ALTER TABLE probabilistic_labels ADD COLUMN positive_votes INTEGER NOT NULL DEFAULT 0"))
    if "negative_votes" not in columns:
        conn.execute(text("ALTER TABLE probabilistic_labels ADD COLUMN negative_votes INTEGER NOT NULL DEFAULT 0"))


def _warn_on_orphan_rows(conn) -> None:
    for table_name in _TABLES_NEEDING_PROJECT_ID:
        try:
            count_row = conn.execute(
                text(f"SELECT COUNT(*) FROM {table_name} WHERE project_id IS NULL")
            ).first()
        except Exception:
            continue
        count = int(count_row[0]) if count_row else 0
        if count:
            logger.warning(
                "Found %d rows in %s with NULL project_id. They will not be "
                "returned by any project-scoped endpoint. Either DELETE them "
                "or assign them to a project with: UPDATE %s SET project_id "
                "= '<id>' WHERE project_id IS NULL;",
                count,
                table_name,
                table_name,
            )
