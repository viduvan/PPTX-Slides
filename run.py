"""
Run Script — Entry point to start the PPTX-Slides API server.
Developed by ChimSe (viduvan) - https://github.com/viduvan
Completed: February 27, 2026

Usage:
    python run.py

Or with uvicorn directly:
    uvicorn api.app:app --host 0.0.0.0 --port 8000 --reload
"""
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api.app:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
