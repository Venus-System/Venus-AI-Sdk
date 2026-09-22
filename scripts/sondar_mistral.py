"""Sonda a Mistral: lista modelos disponíveis e testa 3 chamadas espaçadas, mostrando os headers de limite."""
import os, time, httpx
from dotenv import load_dotenv
load_dotenv()
h = {"Authorization": f"Bearer {os.environ['MISTRAL_API_KEY']}"}
r = httpx.get("https://api.mistral.ai/v1/models", headers=h, timeout=20)
print("modelos:", r.status_code, sorted(m["id"] for m in r.json().get("data", []))[:40] if r.is_success else r.text[:200])
for modelo in ["mistral-small-latest", "mistral-medium-latest", "ministral-8b-latest", "ministral-14b-latest",
               "magistral-small-latest", "mistral-small-2603", "codestral-latest"]:
    r = httpx.post("https://api.mistral.ai/v1/chat/completions", headers=h, timeout=30,
                   json={"model": modelo, "messages": [{"role": "user", "content": "diga ok"}], "max_tokens": 5})
    lim = {k.replace("x-ratelimit-", ""): v for k, v in r.headers.items() if "ratelimit" in k.lower()}
    print(modelo, r.status_code, lim, r.text[:90].replace("\n", " "))
    time.sleep(2)
