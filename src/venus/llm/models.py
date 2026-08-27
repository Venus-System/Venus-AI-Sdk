import os
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from venus.config.settings import GEMINI_API_KEY, GROQ_API_KEY
# ==============================================================================
# MODELOS E AGENTES  (sem checkpointer — a memória fica no grafo)
# ==============================================================================
llm_gemini = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0.7,
    top_p=0.95,
    api_key=GEMINI_API_KEY,
)

llm_groq = ChatGroq(
    model="openai/gpt-oss-120b",
    temperature=0.7,
    api_key=GROQ_API_KEY,
)

llm_especialista = llm_gemini.with_fallbacks([llm_groq])   # se o Gemini falhar, o Groq assume

llm_rapido = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0.0,
    api_key=GROQ_API_KEY,
)