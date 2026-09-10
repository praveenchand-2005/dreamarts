"""Dreamarts Gunicorn bootstrap.
Loads additive runtime extensions before gunicorn serves app:app.
"""

def on_starting(server):
    import app
    import dreamarts_workflow_runtime
    dreamarts_workflow_runtime.register(app.app)
