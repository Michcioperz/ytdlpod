from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse
from feedgen.feed import FeedGenerator
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend
from fastapi_cache.decorator import cache
from pathlib import Path
import tempfile
from urllib.parse import urlencode
from yt_dlp import YoutubeDL

base_config = {
    "extract_flat": "in_playlist",
    'format': 'bestaudio/best',
    'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}]
}
ydl: YoutubeDL = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global ydl
    FastAPICache.init(InMemoryBackend())
    with YoutubeDL(base_config) as ydl_:
        ydl = ydl_
        yield


app = FastAPI(lifespan=lifespan)

@app.get("/infojson")
@cache(expire=5*60)
def infojson(source: str):
    info = ydl.extract_info(source, download=False)
    return ydl.sanitize_info(info)
    # return info


@app.get("/feed")
async def feed(source: str, request: Request):
    json = await infojson(source)
    fg = FeedGenerator()
    fg.load_extension("podcast")
    fg.id(str(request.url))
    fg.title(json["title"])
    fg.link(href=source, rel="alternate")
    fg.link(href=str(request.url), rel="self")
    fg.description(json["description"] or json["title"])
    for entry_stub in json["entries"]:
        entry = await infojson(entry_stub["url"])
        if entry["release_timestamp"]:
            fe = fg.add_entry()
            entry_url = str(request.url_for("episode").replace(
                query=urlencode((("source", entry["original_url"]),))))
            fe.id(entry_url)
            fe.title(entry["title"])
            fe.description(entry["description"] or entry["title"])
            fe.enclosure(entry_url, 0, 'audio/mpeg')
            # fe.updated(entry["timestamp"])
            fe.published(datetime.fromtimestamp(entry["release_timestamp"], timezone.utc))
    return Response(content=fg.atom_str(pretty=True), media_type="application/atom+xml; charset=utf-8")


@app.get("/episode", name="episode")
async def episode(source: str):
    with tempfile.NamedTemporaryFile("w") as infofile:
        json.dump(await infojson(source), infofile)
        infofile.flush()
        with tempfile.TemporaryDirectory() as tmpdir:
            with YoutubeDL(base_config | {"paths": {"home": str(tmpdir)}}) as ydl:
                ydl.download_with_info_file(infofile.name)
            path = Path(tmpdir)
            [file] = list(path.iterdir())
            content = file.read_bytes()
            return Response(content=content, media_type="audio/mpeg", headers={"content-length": f"{len(content)}"})


def main():
    import uvicorn
    config = uvicorn.Config("ytdlpod:app")
    server = uvicorn.Server(config)
    server.run()
