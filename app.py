import re
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

class LinkRequest(BaseModel):
    url: str

BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://gofile.io",
    "Referer": "https://gofile.io/"
}

def extract_content_id(url: str) -> str:
    match = re.search(r"gofile\.io/d/([a-zA-Z0-9]+)", url)
    if not match:
        raise HTTPException(status_code=400, detail="Invalid Gofile URL format")
    return match.group(1)

async def create_gofile_guest_token(client: httpx.AsyncClient) -> str:
    try:
        response = await client.post("https://api.gofile.io/accounts", headers=BASE_HEADERS)
        data = response.json()
        if data.get("status") == "ok":
            return data["data"]["token"]
        raise HTTPException(status_code=500, detail="Failed to create Gofile guest token")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Token generation error: {str(e)}")

@app.post("/get-stream-link")
async def get_stream_link(request: LinkRequest, req_info: Request):
    content_id = extract_content_id(request.url)
    
    async with httpx.AsyncClient(timeout=15.0) as client:
        token = await create_gofile_guest_token(client)
        headers = BASE_HEADERS.copy()
        headers["Authorization"] = f"Bearer {token}"
        
        api_url = f"https://api.gofile.io/getContents?contentId={content_id}"
        try:
            response = await client.get(api_url, headers=headers)
            res_data = response.json()
            
            if res_data.get("status") != "ok":
                raise HTTPException(status_code=400, detail="Gofile API Error")
                
            contents = res_data["data"]["contents"]
            if not contents:
                raise HTTPException(status_code=404, detail="No files found")
            
            file_id = list(contents.keys())[0]
            file_info = contents[file_id]
            
            direct_link = file_info.get("link")
            if not direct_link:
                raise HTTPException(status_code=500, detail="No link generated")
            
            base_url = str(req_info.base_url).rstrip("/")
            proxy_url = f"{base_url}/proxy-stream?stream_url={direct_link}&token={token}"
                
            return {
                "status": "success",
                "file_name": file_info.get("name"),
                "stream_url": proxy_url
            }
            
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed: {str(e)}")

@app.get("/proxy-stream")
async def proxy_stream(stream_url: str, token: str, request: Request):
    headers = BASE_HEADERS.copy()
    headers["Authorization"] = f"Bearer {token}"
    
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header

    client = httpx.AsyncClient(timeout=60.0)
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
            async for chunk in resp.aiter_bytes(chunk_size=64 * 1024):
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(stream_generator(), status_code=resp.status_code, headers=response_headers)
    
