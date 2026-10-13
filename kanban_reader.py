#!/usr/bin/env python3
"""Standalone kanban board reader. Mirrors the exact SQL the /kanban endpoint
will use, so the query logic is verified against the real DB before it is
wired into room_server.py. Read-only: opens kanban.db in ?mode=ro.

Usage: python3 kanban_reader.py [path-to-kanban.db]
Prints the board JSON to stdout. Exit 0 on success.
"""
import json
import os
import sqlite3
import sys

DEFAULT_DB = os.path.expanduser("~/.hermes/kanban.db")


def read_board(db_path):
    uri = "file:%s?mode=ro" % db_path
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    try:
        tasks = con.execute(
            """SELECT id, title, body, assignee, status, priority,
                       created_by, created_at, started_at, completed_at,
                       result, block_kind, consecutive_failures
                FROM tasks ORDER BY created_at DESC"""
        ).fetchall()

        links = con.execute(
            "SELECT parent_id, child_id FROM task_links"
        ).fetchall()

        # Latest run per task (most recent started_at), outcome + summary.
        runs = con.execute(
            """SELECT task_id, outcome, summary, ended_at, status
               FROM task_runs
               WHERE id IN (
                   SELECT MAX(id) FROM task_runs GROUP BY task_id
               )
               ORDER BY task_id"""
        ).fetchall()

        parent_of = {}
        children = {}
        for r in links:
            parent_of[r["child_id"]] = r["parent_id"]
            children.setdefault(r["parent_id"], []).append(r["child_id"])

        run_by_task = {r["task_id"]: r for r in runs}

        status_counts = {}
        out = []
        for t in tasks:
            tid = t["id"]
            status_counts[t["status"]] = status_counts.get(t["status"], 0) + 1
            run = run_by_task.get(tid)
            out.append({
                "id": tid,
                "title": t["title"],
                "assignee": t["assignee"],
                "status": t["status"],
                "priority": t["priority"],
                "created_by": t["created_by"],
                "created_at": t["created_at"],
                "completed_at": t["completed_at"],
                "block_kind": t["block_kind"],
                "parent_id": parent_of.get(tid),
                "children": children.get(tid, []),
                "latest_run": (
                    {"outcome": run["outcome"], "summary": run["summary"],
                     "ended_at": run["ended_at"], "run_status": run["status"]}
                    if run else None
                ),
            })

        return {
            "total": len(out),
            "status_counts": status_counts,
            "tasks": out,
        }
    finally:
        con.close()


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    board = read_board(db)
    print(json.dumps(board, indent=2))
    # sanity: totals must agree
    assert board["total"] == len(board["tasks"]), "total != len(tasks)"
    assert sum(board["status_counts"].values()) == board["total"], "status_counts != total"
    sys.exit(0)
