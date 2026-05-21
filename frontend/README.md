# Frontend

Vue 3 chat UI for the minimal LangGraph Agent backend.

## Commands

```sh
npm install
npm run dev
npm run build
```

The app calls `/api/agent/chat/stream` and renders SSE tokens as they arrive.

## Streaming in dev

Development uses `frontend/.env.development` to call FastAPI directly (`http://127.0.0.1:8000/api`), because Vite's `/api` proxy may buffer SSE and make the UI look non-streaming.

After changing env files, restart `npm run dev`.
