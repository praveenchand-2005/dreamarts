"""AI observability extension for Dreamarts.
Provides operational metrics from persisted council runs without adding a new dependency.
"""
import datetime
from flask import request, jsonify


def register(app):
    import app as core

    def token():
        auth = request.headers.get("Authorization", "")
        return auth.split(" ", 1)[1] if auth.startswith("Bearer ") else None

    @app.get("/api/admin/ai/observability")
    def ai_observability():
        t = token()
        if not t:
            return jsonify(error="Unauthorized"), 401
        limit = min(int(request.args.get("limit", "100")), 500)
        rows = core.ai_repo_select("ai_council_runs", "*", t, limit=limit) or []
        total = len(rows)
        completed = sum(1 for r in rows if str(r.get("status", "")).upper() == "COMPLETED")
        failed = sum(1 for r in rows if str(r.get("status", "")).upper() in ("FAILED", "ERROR"))
        pending = total - completed - failed
        latencies = [float(r.get("latency_ms")) for r in rows if r.get("latency_ms") is not None]
        reliability = []
        for r in rows:
            result = r.get("result") or {}
            outputs = result.get("agent_outputs") or {}
            for out in outputs.values():
                rel = (out or {}).get("reliability", {}).get("reliability_score")
                if rel is not None:
                    reliability.append(float(rel))
        return jsonify(ok=True, metrics={
            "sample_size": total,
            "completed": completed,
            "failed": failed,
            "pending_or_other": pending,
            "success_rate": round(completed / total, 3) if total else None,
            "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
            "avg_agent_reliability": round(sum(reliability) / len(reliability), 3) if reliability else None,
            "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
        })

    @app.get("/api/admin/ai/observability/health")
    def ai_observability_health():
        return jsonify(ok=True, service="ai_observability", timestamp=datetime.datetime.utcnow().isoformat() + "Z")

    return app
