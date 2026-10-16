from fastapi import FastAPI


app = FastAPI(title="Consultation Service", version="0.1.0")


@app.get("/api/v1/consultations/health")
def health():
    return {"status": "ok", "service": "consultation-service"}

