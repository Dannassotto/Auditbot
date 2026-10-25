import json
ll = [json.loads(l) for l in open("logs/llamadas.jsonl", encoding="utf-8")]
dec = [json.loads(l) for l in open("logs/decisiones.jsonl", encoding="utf-8")]
total = sum(x["costo_usd"] for x in ll)
print(len(ll), "llamadas |", round(total, 4), "USD |", len(dec), "decisiones |", round(total / len(dec), 4), "USD por orden")