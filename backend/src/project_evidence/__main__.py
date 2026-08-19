import uvicorn

uvicorn.run("project_evidence.main:app", host="0.0.0.0", port=8000, reload=False)
