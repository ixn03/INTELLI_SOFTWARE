from fastapi import FastAPI, UploadFile, File

app = FastAPI()


@app.get("/health")
def health():
    return {"status": "INTELLI backend running"}


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    return {
        "filename": file.filename,
    }
