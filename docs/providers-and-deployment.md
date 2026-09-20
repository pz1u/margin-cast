# LLM Provider와 배포 준비

MarginCast Agent Runtime은 LLMProvider.generate(messages, tools) -> LLMResponse만 사용한다.
src.llm_provider_factory.create_llm_provider()가 LLM_PROVIDER 환경변수에 따라 Provider를 만든다.
Ollama와 OpenAI 모두 계산 Tool을 직접 실행하지 않으며, Runtime이 기존 execute_tool() 경계에서
계산 결과를 받는다.

## Provider 설정

로컬 Ollama는 기존 기본값이다.

~~~powershell
$env:LLM_PROVIDER="ollama"
$env:OLLAMA_BASE_URL="http://localhost:11434"
$env:OLLAMA_MODEL="qwen3:1.7b"
python -m src.http_api
~~~

OpenAI는 Responses API의 Function Calling으로 Tool Call을 받고, 최종 정성 설명은
text.format의 strict JSON Schema Structured Outputs로 받는다. 모델명과 키는 코드에 두지 않는다.

~~~powershell
$env:LLM_PROVIDER="openai"
$env:OPENAI_API_KEY="<server-secret>"
$env:OPENAI_MODEL="gpt-5.6-luna"
python -m src.http_api
~~~

gpt-5.6-luna는 공식 모델 문서 기준 Responses API, Function Calling과 Structured Outputs를
지원한다. 현재 공개 요금은 백만 토큰당 입력 0.20달러, 캐시 입력 0.02달러, 출력 1.20달러다.

- [GPT-5.6 Luna 모델과 요금](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [Function Calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)

공통 Tool Schema에는 엔진 실행 기본값처럼 선택 필드가 있다. OpenAI strict Function Calling은
모든 속성을 required로 선언해야 하므로 Tool Call에는 strict: false를 명시하고 기존 Runtime의
JSON Schema 검증을 최종 경계로 유지한다. 최종 설명 Schema는 모든 필드가 필수이므로
strict: true를 사용한다.

실제 OpenAI 선택 테스트는 키가 있는 환경에서만 실행한다.

~~~powershell
$env:RUN_OPENAI_INTEGRATION="1"
python -m unittest tests.test_openai_integration -v
~~~

키가 없으면 테스트는 skip한다. 가짜 키로 외부 호출 성공을 만들지 않는다.

## Provider benchmark

같은 실제 가격 Agent 요청을 최소 다섯 번 실행해 Tool 선택, 인자, Policy, latency와 토큰을
비교한다.

~~~powershell
python -m scripts.benchmark_agent_providers --provider ollama --runs 5
python -m scripts.benchmark_agent_providers --provider openai --runs 5
~~~

OpenAI 결과는 API가 반환한 input, cached input, output token을 합산한다. 모델이 정확히
gpt-5.6-luna일 때만 위 공식 요금으로 예상 비용을 계산한다. 다른 모델은 잘못된 비용을
표시하지 않고 null을 반환한다. 이 스크립트는 API key, 전체 대화, ToolResult 또는 LLM 설명
전문을 출력하지 않는다.

## 배포 실행 계약

플랫폼 시작 명령은 다음 한 줄이다.

~~~text
python -m src.http_api
~~~

배포 환경에서는 다음을 설정한다.

~~~text
HOST=0.0.0.0
PORT=<platform assigned port>
LLM_PROVIDER=openai
OPENAI_API_KEY=<secret>
OPENAI_MODEL=gpt-5.6-luna
MARGINCAST_STATE_DIR=<writable mounted directory>
KAKAO_REST_API_KEY=<secret>
KMA_SERVICE_KEY=<secret>
~~~

GET /health와 기존 GET /api/health가 같은 비민감 상태를 반환한다. 정적 UI와 API는 같은
서버와 origin에서 제공하므로 현재 CORS 설정은 필요하지 않다. 브라우저 코드에는 OpenAI,
Kakao, KMA 키를 넣지 않는다.

MARGINCAST_STATE_DIR에는 다음 로컬 JSON이 저장된다.

- audit/agent_audit.json
- feedback/experiment_feedback.json
- store/store_profile.json

각 저장소는 같은 프로세스 안에서 lock과 atomic replace를 사용한다. 현재 서버와 메모리 Session
Store는 해커톤 MVP의 단일 프로세스·단일 worker 전제다. 여러 worker를 띄우면 세션과 파일 lock이
공유되지 않는다. 배포 플랫폼의 로컬 디스크가 ephemeral이면 재시작 때 이 상태가 사라지므로,
데모에서 상태 유지가 필요하면 쓰기 가능한 persistent volume을 MARGINCAST_STATE_DIR에 연결한다.

서버는 다음 outbound HTTPS 연결이 필요하다.

- OpenAI Responses API
- Kakao Local API
- 기상청 단기예보 API

현재 동기식 표준 라이브러리 HTTP 서버는 요청 하나가 Provider 응답을 기다리는 동안 해당 worker를
점유한다. OpenAI E2E benchmark 결과를 확인한 뒤에도 데모 동시 요청 수가 늘면 서버 구조를 별도
단계에서 검토한다.