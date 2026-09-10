"""Governed workflow runtime extension for Dreamarts.
Loaded by gunicorn.conf.py before app:app is served.
"""
import datetime, uuid, json
from flask import request, jsonify


def register(app):
    supabase_request = app.view_functions.get("_supabase_request")
    # Resolve helpers from the application module so this extension stays additive.
    import app as core

    def auth_token():
        auth = request.headers.get("Authorization", "")
        return auth.split(" ", 1)[1] if auth.startswith("Bearer ") else None

    def persist(token, row):
        try:
            return core.ai_repo_insert("ai_workflow_runs", row, token)
        except Exception:
            return None

    def make_plan(goal):
        if hasattr(core, "create_agent_execution_plan"):
            return core.create_agent_execution_plan(goal)
        return {
            "goal": goal,
            "status": "PLANNED",
            "steps": [
                {"order": 1, "action": "investigate_context", "tool": "semantic_search", "risk": "LOW"},
                {"order": 2, "action": "verify_evidence", "tool": "evidence_verify", "risk": "LOW"},
                {"order": 3, "action": "evaluate_options", "tool": "scenario_simulate", "risk": "LOW"},
                {"order": 4, "action": "propose_controlled_action", "tool": "experiment_create", "risk": "MEDIUM"},
            ],
        }

    def execute_low_risk_step(token, step, context):
        tool = step.get("tool")
        args = step.get("arguments") or {}
        if hasattr(core, "evaluate_tool_request"):
            gate = core.evaluate_tool_request(tool, args)
        else:
            gate = {"allowed": step.get("risk") == "LOW", "risk": step.get("risk", "HIGH")}
        if not gate.get("allowed"):
            return {"status": "AWAITING_APPROVAL", "gate": gate, "step": step}
        if hasattr(core, "execute_agent_tool"):
            result = core.execute_agent_tool(tool, args, token)
            return {"status": "COMPLETED", "gate": gate, "step": step, "result": result}
        return {"status": "BLOCKED", "gate": gate, "step": step, "reason": "Tool executor unavailable"}

    @app.post("/api/admin/ai/workflows/execute")
    def execute_governed_workflow():
        token = auth_token()
        if not token:
            return jsonify(error="Unauthorized"), 401
        body = request.get_json(silent=True) or {}
        goal = str(body.get("goal", "")).strip()
        if not goal:
            return jsonify(error="goal is required"), 400
        run_id = "wf_" + uuid.uuid4().hex[:12]
        plan = make_plan(goal)
        context = body.get("context") or {}
        steps = []
        overall = "COMPLETED"
        for step in plan.get("steps", []):
            if str(step.get("risk", "HIGH")).upper() != "LOW":
                result = {"status": "AWAITING_APPROVAL", "step": step, "reason": "Medium/high-risk actions require founder authorization."}
                overall = "AWAITING_APPROVAL"
            else:
                result = execute_low_risk_step(token, step, context)
                if result.get("status") != "COMPLETED":
                    overall = result.get("status", "BLOCKED")
            steps.append(result)
            if overall in ("BLOCKED", "FAILED"):
                break
        row = {
            "id": run_id,
            "goal": goal,
            "status": overall,
            "plan": plan,
            "steps": steps,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            "governance": {"approval_required_for": ["MEDIUM", "HIGH"], "autonomous_execution": False},
        }
        stored = persist(token, row)
        return jsonify(ok=True,run_id=run_id,status=overall,workflow=row,persistence_mode="postgres" if stored is not None else "fallback")

    @app.post("/api/admin/ai/workflows/approve")
    def approve_governed_workflow():
        token = auth_token()
        if not token:
            return jsonify(error="Unauthorized"), 401
        body = request.get_json(silent=True) or {}
        run_id = str(body.get("run_id", "")).strip()
        if not run_id:
            return jsonify(error="run_id is required"), 400
        # Approval is recorded explicitly; actual external actions remain behind the existing tool gate.
        row = {
            "id": "approval_" + uuid.uuid4().hex[:12],
            "workflow_id": run_id,
            "approved_by": "FOUNDER",
            "approved_at": datetime.datetime.utcnow().isoformat() + "Z",
            "scope": body.get("scope") or "specific_workflow",
            "status": "APPROVED",
        }
        stored = persist(token, row)
        return jsonify(ok=True,approval=row,persistence_mode="postgres" if stored is not None else "fallback")

    @app.get("/api/admin/ai/workflows/health")
    def workflow_runtime_health():
        return jsonify(ok=True,runtime="governed_workflow_runtime",approval_model="founder_required_for_medium_high_risk",timestamp=datetime.datetime.utcnow().isoformat()+"Z")

    return app
