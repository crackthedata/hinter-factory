# Hinter ML service

Run locally:

```bash
cd services/ml
python -m venv .venv
.venv\Scripts\activate  # Windows
pip install -e .
uvicorn app.main:app --reload --port 8000
```

SQLite database file defaults to `./data/hinter.db` (created on first boot).
