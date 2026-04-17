# menuboard-image-api

Local API wrapper for MiniMax image generation, used by menuboard.online.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your MINIMAX_API_KEY
```

## Run

```bash
uvicorn main:app --host 0.0.0.0 --port 3001
```

## Endpoints

- `GET /health` — Health check
- `POST /generate` — Generate an image

### POST /generate

**Body:**
```json
{
  "prompt": "warm cafe background",
  "aspect_ratio": "16:9"
}
```

**Headers (optional):**
- `X-Session-ID` — custom session identifier for rate limiting

**Response:**
```json
{
  "image_urls": ["https://..."],
  "model": "image-01",
  "prompt": "warm cafe background",
  "aspect_ratio": "16:9"
}
```

## Rate Limiting

6 requests per session (session tracked via cookie or `X-Session-ID` header).
