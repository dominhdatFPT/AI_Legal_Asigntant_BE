from fastapi import FastAPI


app = FastAPI(title="Location Service", version="0.1.0")


@app.get("/api/v1/locations/health")
def health():
    return {"status": "ok", "service": "location-service"}
