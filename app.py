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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
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
    content_id = extract_content_id(request.url)
    
    # Gofile ke rules ke hisab se, public web data fetch karne ke liye guest token header me dalna compulsory nahi hota
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        # Direct content details fetch karne ki koshish web gateway se
        api_url = f"https://api.gofile.io/getContents?contentId={content_id}"
        try:
            response = await client.get(api_url, headers=BASE_HEADERS)
            
            # Agar direct call block hoti hai, toh backup raw endpoint wrapper call use karenge
            if response.status_code != 200 or "application/json" not in response.headers.get("content-type", ""):
                raise HTTPException(status_code=403, detail="Gofile dashboard blocked Render server IP. Try again or use client-side fetch.")
                
            res_data = response.json()
            if res_data.get("status") != "ok":
                raise HTTPException(status_code=400, detail=f"Gofile API status error: {res_data.get('status')}")
                
            contents = res_data["data"]["contents"]
            if not contents:
                raise HTTPException(status_code=404, detail="No files found inside this link")
            
            file_id = list(contents.keys())[0]
            file_info = contents[file_id]
            
            direct_link = file_info.get("link")
            if not direct_link:
                raise HTTPException(status_code=500, detail="Gofile did not provide direct stream link")
            
            base_url = str(req_info.base_url).rstrip("/")
            # Hame token ki zaroorat nahi agar session context static node standard use karega
            proxy_url = f"{base_url}/proxy-stream?stream_url={direct_link}"
                
            return {
                "status": "success",
                "file_name": file_info.get("name"),
                "stream_url": proxy_url
            }
            
        except httpx.HTTPError as he:
            raise HTTPException(status_code=500, detail=f"Network Error: {str(he)}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to process content: {str(e)}")

@app.get("/proxy-stream")
async def proxy_stream(stream_url: str, request: Request):
    headers = BASE_HEADERS.copy()
    
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
            async for chunk in resp.aiter_bytes(chunk_size=128 * 1024): # Fast delivery chunk
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(stream_generator(), status_code=resp.status_code, headers=response_headers)
    
