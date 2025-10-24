# Vox-9 Web POC
One-user FastAPI demo with a fake pipeline. Swap in your real functions later.

## Dev
````
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export BASIC_USER=admin BASIC_PASS=changeme
uvicorn app.main:app --reload
````

## Docker
````
docker build -t vox9-poc .
docker run --rm -p 8000:8000 -e BASIC_USER=admin -e BASIC_PASS=changeme vox9-poc
````

## The layout:
````
vox9-web-poc/
├─ app/
│  ├─ main.py
│  ├─ auth.py
│  ├─ models.py
│  ├─ pipeline_adapter.py
│  ├─ settings.py
│  ├─ static/
│  │  └─ index.html
├─ requirements.txt
├─ Dockerfile
├─ .env.example
├─ README.md
````
