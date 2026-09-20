# Provider benchmark

측정일: 2026-09-20
질문: 치킨마요를 9,500원으로 올리면 어때?
반복: Provider별 다섯 회

| 항목 | Ollama qwen3:1.7b | OpenAI gpt-5.6-luna |
|---|---:|---:|
| COMPLETED | 5/5 | 5/5 |
| Tool 선택 성공 | 5/5 | 5/5 |
| Tool 인자 정확 | 5/5 | 5/5 |
| 최초 LLM Policy PASS | 2/5 | 5/5 |
| H0 fallback | 3/5 | 0/5 |
| 최종 REJECTED | 0/5 | 0/5 |
| 평균 first Provider | 4,285 ms | 1,424 ms |
| 평균 second Provider | 25,002 ms | 3,149 ms |
| 평균 total | 29,611 ms | 4,880 ms |
| total p50 | 29,359 ms | 4,904 ms |
| total p95 | 31,220 ms | 5,333 ms |
| input token | 23,258 | 20,506 |
| cached input token | 해당 없음 | 0 |
| output token | 812 | 1,777 |
| 설명 출력량 | 514자 | 1,327자 |
| 추정 비용 | 로컬 실행 | $0.0062336 |

OpenAI 열은 실제 API와 `gpt-5.6-luna`를 사용해 같은 요청을 다섯 회 측정한 결과다.
추정 비용은 API usage와 공식 모델 요금을 사용했으며 실제 청구 금액과 차이가 있을 수 있다.

~~~powershell
$env:LLM_PROVIDER="openai"
$env:OPENAI_MODEL="gpt-5.6-luna"
python -m scripts.benchmark_agent_providers --provider openai --runs 5
~~~

Ollama token은 실제 응답의 prompt_eval_count와 eval_count 합계다. OpenAI 비용은 API usage와
공식 gpt-5.6-luna 요금이 모두 있을 때만 계산한다.
