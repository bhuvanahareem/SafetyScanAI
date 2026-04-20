# Cloud Deployment & Firewall Bypass Strategy

This plan outlines the migration of SafetyScanAI from a local/tunneled architecture to a robust, cloud-hosted environment. This eliminates the dependency on fragile tunnels (Ngrok/LocalTunnel) for the frontend and n8n, ensuring the demo works even behind strict university firewalls.

## Connectivity Audit & Fragility Analysis

| Connection Path | Current Tech | Fragility Level | Risk | Mitigation |
| :--- | :--- | :--- | :--- | :--- |
| **Browser → Local Server** | `localhost:8000` | High | Only works on your laptop. | Deploy to Render/Railway for a public URL. |
| **Server → Colab GPU** | `LocalTunnel` | Critical | **Domain Blocking**. Universities often block `.loca.lt`. | Force **Cloudflare Tunnel** (looks like standard CDN traffic). |
| **Server → n8n** | `Ngrok` | High | **Tunnel Latency**. Double-tunneling (Ngrok to Local) adds lag. | Deploy n8n to the cloud (Render/Railway). |

## Firewall Bypass Solutions

### 1. Cloud-Native Hosting (Render/Railway)
Instead of serving from your laptop, we will deploy the **FastAPI Backend (app.py)** and **static files** to Render or Railway. 
- **Benefit**: You get a stable `https://*.onrender.com` or `*.up.railway.app` URL. These are rarely blocked by campus firewalls.
- **WebSocket**: These platforms handle `wss://` (Secure WebSockets) natively.

### 2. Cloudflare Tunnels for Colab
Google Colab requires a tunnel to receive incoming requests from your cloud backend. 
- **Change**: We will prioritize `cloudflared` over `LocalTunnel`. Cloudflare traffic is almost never blocked by firewalls because it shares infrastructure with millions of legitimate websites.

## State Management & Resilience

- **WebSocket Reconnection**: The existing logic in `app.js` is already robust with exponential backoff.
- **Latency Buffering**: We will add a "Keep-Alive" heartbeat in `app.py` to ensure the cloud proxy doesn't drop the WebSocket connection during long Tier 2 analysis (which can take 60s+).

---

## Deployment Roadmap (Step-by-Step)

### Phase 1: Containerizing the Local Backend [NEW]
We need a `Dockerfile` to deploy your `app.py` to the cloud.

#### [NEW] [Dockerfile](file:///c:/Users/bhuva/Documents/SafetyScanAI/Dockerfile)
- Base image: `python:3.10-slim`.
- Install dependencies from `requirements_local.txt`.
- Expose port `8000`.

### Phase 2: Deploying n8n to the Cloud
- Use a **Railway 1-Click Template** for n8n.
- **Why**: This gives n8n a permanent URL (e.g., `n8n-production.up.railway.app`) and eliminates your local Docker/Ngrok setup for the demo.

### Phase 3: Colab Tunnel Optimization
#### [MODIFY] [colab_backend/ai_pipeline.py](file:///c:/Users/bhuva/Documents/SafetyScanAI/colab_backend/ai_pipeline.py)
- Swap the startup logic to attempt **Cloudflare Tunnel** first.
- Explicitly log the Cloudflare URL to make it easy to copy-paste.

### Phase 4: Final Demo Configuration
1. Deploy `app.py` to Render/Railway.
2. Update Render/Railway Environment Variables:
   - `COLAB_API_URL`: (The URL from Cloudflare/Colab).
   - `N8N_WEBHOOK_URL`: (The URL from your cloud n8n).
   - `API_SECRET_KEY`: (Your shared secret).

---

## Demo Day Checklist

1. [ ] **Start Colab**: Run the notebook, wait for the Cloudflare Tunnel URL.
2. [ ] **Update Backend**: Paste the Colab URL into your Render/Railway Environment Variables.
3. [ ] **Launch Dashboard**: Open your stable cloud URL (e.g., `https://safetyscanai.up.railway.app`).
4. [ ] **Check Health**: Confirm the "Colab: Connected" status dot is green.
5. [ ] **Run Demo**: Monitor live via WebSocket; emails will trigger via the cloud n8n.

## Open Questions
1. **Cloud Platform**: Do you have a preference between **Railway** (usually easier for n8n) or **Render**?
2. **Persistent Storage**: For the demo, is it okay if uploaded images are temporary (cleared on redeploy), or do you need them to persist?
