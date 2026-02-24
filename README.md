### Short GitHub Description
AI-powered support message triage demo that classifies customer requests, sets priority, and generates structured JSON using a schema-driven GPT pipeline with manual-based keyword retrieval (no embeddings).

### README Intro (copy/paste)
This project is a lightweight **support operations copilot** built with **Flask + OpenAI**.  
It takes customer messages (bug report / feature request / question), retrieves the **top 3 relevant chunks** from an internal manual using simple keyword matching, and generates a structured extraction JSON based on a fixed schema.

It includes:
- A demo frontend to select request type and run the pipeline
- Friendly step-by-step pipeline logs
- Schema-based output formatting
- Retrieval-augmented prompting from local knowledge files (no vector DB)

### Key Features
- Schema-constrained extraction (`request_type`, `priority`, `summary`, `next action`, `missing questions`)
- Hallucination reduction via manual chunk retrieval (`top 3` chunk IDs + text in prompt)
- Local knowledge assets in `support_knowledge_base/`
- Simple, inspectable architecture for demos and iteration
