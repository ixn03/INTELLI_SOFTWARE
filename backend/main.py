from fastapi import FastAPI, File, UploadFile


app = FastAPI()


@app.get("/health")
def health():
    return {"status": "INTELLI backend running"}


@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    return {
        "filename": file.filename
    }

