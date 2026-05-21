# LangGraph Agent Demo

一个最小化的 LangChain + LangGraph 对话 Agent 示例。

## 后端

- Python 3.12
- FastAPI
- LangChain 1.x
- LangGraph
- OpenAI 兼容聊天模型
- 使用 LangGraph `MemorySaver` 和 `thread_id` 管理会话记忆

## 前端

- Vue 3
- JavaScript
- Tailwind CSS
- daisyUI
- SSE 流式输出

## 环境变量

在 `backend/.env` 中配置：

```env
APP_NAME=LangGraph Agent Backend
CORS_ORIGINS=*
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
LLM_TEMPERATURE=0.2
LLM_CONTEXT_MESSAGES=12
```
