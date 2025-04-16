from fastapi import FastAPI

app = FastAPI()

@app.get("/")
async def root():
    return {"greeting": "Hello, World!", "message": "LESSSSSGAWWWW, fastapi backend  naten guys deployed to sa railway"}
