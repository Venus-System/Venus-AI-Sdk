"""Mede a latência de cada elo das cadeias de LLM (prompt trivial)."""
import asyncio, time
from dotenv import load_dotenv

async def main():
    load_dotenv()
    from venus_sdk.llm.models import _criar_modelo, cadeia_especialista, cadeia_rapida
    for p, m in cadeia_especialista() + cadeia_rapida():
        t = time.perf_counter()
        try:
            r = await asyncio.wait_for(_criar_modelo(p, m, rapido=False).ainvoke("Liste 3 frutas em uma linha."), 60)
            print(f"{p}:{m} {time.perf_counter()-t:.1f}s ok {str(r.content)[:40]!r}", flush=True)
        except Exception as e:
            print(f"{p}:{m} {time.perf_counter()-t:.1f}s ERRO {type(e).__name__}: {str(e)[:150]}", flush=True)
asyncio.run(main())
