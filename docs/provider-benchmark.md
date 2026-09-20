# Provider benchmark

측정일: 2026-09-20
질문: 치킨마요를 9,500원으로 올리면 어때?
반복: Provider별 다섯 회

| 항목 | Ollama qwen3:1.7b | OpenAI gpt-5.6-luna |
|---|---:|---:|
| COMPLETED | 5/5 | 미실행 |
| Tool 선택 성공 | 5/5 | 미실행 |
| Tool 인자 정확 | 5/5 | 미실행 |
| 최초 LLM Policy PASS | 2/5 | 미실행 |
| H0 fallback | 3/5 | 미실행 |
| 최종 REJECTED | 0/5 | 미실행 |
| 평균 first Provider | 4,285 ms | 미실행 |
| 평균 second Provider | 25,002 ms | 미실행 |
| 평균 total | 29,611 ms | 미실행 |
| total p50 | 29,359 ms | 미실행 |
| total p95 | 31,220 ms | 미실행 |
| input token | 23,258 | 미실행 |
| output token | 812 | 미실행 |
| 설명 출력량 | 514자 | 미실행 |

OpenAI 열은 OPENAI_API_KEY와 OPENAI_MODEL이 현재 환경과 프로젝트 .env에 없어 호출하지 않았다.
가짜 키나 Mock 결과로 채우지 않았다. 키를 준비한 뒤 아래 명령으로 같은 요청 다섯 회를 측정한다.

~~~powershell
$env:LLM_PROVIDER="openai"
$env:OPENAI_MODEL="gpt-5.6-luna"
python -m scripts.benchmark_agent_providers --provider openai --runs 5
~~~

Ollama token은 실제 응답의 prompt_eval_count와 eval_count 합계다. OpenAI 비용은 API usage와
공식 gpt-5.6-luna 요금이 모두 있을 때만 계산한다.
