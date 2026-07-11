import re
import os
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Render ke Environment Variables se token secure tarike se uthayega
# Taki GitHub par open me token leak na ho
RAW_TOKEN = os.environ.get("GOFILE_TOKEN", "")
GOFILE_ACCOUNT_TOKEN = RAW_TOKEN.strip() if RAW_TOKEN else ""

class LinkRequest(BaseModel):
    url: str

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://gofile.io",
    "Referer": "https://gofile.io/"
}

def extract_content_id(url: str) -> str:
    match = re.search(r"gofile\.io/d/([a-zA-Z0-9]+)", url)
    if not match:
        raise HTTPException(status_code=400, detail="Invalid Gofile URL format")
    return match.group(1)

@app.post("/get-stream-link")
async def get_stream_link(request: LinkRequest, req_info: Request):
    if not GOFILE_ACCOUNT_TOKEN:
        raise HTTPException(
            status_code=500, 
            detail="Render Dashboard par GOFILE_TOKEN env variable missing he ya khali he!"
        )

    content_id = extract_content_id(request.url)
    
    headers = BASE_HEADERS.copy()
    headers["Authorization"] = f"Bearer {GOFILE_ACCOUNT_TOKEN}"
    
    api_url = f"https://api.gofile.io/getContents?contentId={content_id}"
    
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        try:
            response = await client.get(api_url, headers=headers)
            
            # Agar Gofile direct 404 ya 401 feke toh exact screen par dikhega
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code, 
                    detail=f"Gofile API directly returned status {response.status_code}. Details: {response.text[:150]}"
                )
                
            res_data = response.json()
            if res_data.get("status") != "ok":
                raise HTTPException(
                    status_code=400, 
                    detail=f"Gofile JSON status not ok. Full Response: {res_data}"
                )
                
            contents = res_data["data"]["contents"]
            if not contents:
                raise HTTPException(status_code=404, detail="No files found inside this link folder")
            
            file_id = list(contents.keys())[0]
            file_info = contents[file_id]
            
            direct_link = file_info.get("link")
            if not direct_link:
                raise HTTPException(status_code=500, detail="Storage node link missing in Gofile response")
            
            base_url = str(req_info.base_url).rstrip("/")
            proxy_url = f"{base_url}/proxy-stream?stream_url={direct_link}"
                
            return {
                "status": "success",
                "file_name": file_info.get("name"),
                "stream_url": proxy_url
            }
        except HTTPException as he:
            raise he
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Server Crash Logic: {str(e)}")

@app.get("/proxy-stream")
async def proxy_stream(stream_url: str, request: Request):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Authorization": f"Bearer {GOFILE_ACCOUNT_TOKEN}"
    }
    
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=60.0, follow_redirects=True)
    req = client.build_request("GET", stream_url, headers=headers)
    resp = await client.send(req, stream=True)
    
    response_headers = {
        "Accept-Ranges": resp.headers.get("Accept-Ranges", "bytes"),
        "Content-Type": resp.headers.get("Content-Type", "video/mp4"),
        "Content-Length": resp.headers.get("Content-Length"),
    }
    if resp.headers.get("Content-Range"):
        response_headers["Content-Range"] = resp.headers.get("Content-Range")

    async def stream_generator():
        try:
            async for chunk in resp.aiter_bytes(chunk_size=128 * 1024):
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(stream_generator(), status_code=resp.status_code, headers=response_headers)

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
    
