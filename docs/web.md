# MarginCast 웹사이트

`web/`은 매장 운영자가 MarginCast Agent와 가격 전략을 대화로 검토하는 브라우저 화면이다.
별도 프론트엔드 프레임워크 없이 HTML, CSS, JavaScript로 구성하며 `src.http_api`가 정적 파일과
`POST /api/agent/chat`을 함께 제공한다.

## 실행

```powershell
ollama serve
ollama pull qwen3:1.7b
$env:OLLAMA_BASE_URL="http://localhost:11434"
$env:OLLAMA_MODEL="qwen3:1.7b"
.\.venv\Scripts\python.exe -m src.http_api
```

브라우저에서 `http://127.0.0.1:8000`에 접속한다. 실제 데모 전에 가격 질문을 한 번 보내 모델을
warm 상태로 준비한다. 서버 startup에서는 모델이나 사업 Tool을 자동 호출하지 않는다.

## 화면 흐름

1. 메뉴와 변경 가격을 자연어로 입력한다.
2. 가격이 빠지면 `NEEDS_INPUT` 질문을 일반 Agent 메시지로 받고 같은 세션에서 답한다.
3. 요청 중에는 실제 backend progress가 아닌 대기 안내 문구를 단계적으로 표시한다.
4. `COMPLETED`이면 설명과 다음 행동은 `presentation`에서, 계산 숫자는 `facts`에서 각각 표시한다.
5. Decision Engine의 `추천`, `소규모 실험`, `보류` 판단을 결과 카드의 최상단에 표시한다.
6. 기여이익 변화, 개선확률, 예상 범위, 하방 위험, 근거 품질과 데이터 고지를 확인한다.

`REJECTED`는 내부 위반 코드를 숨기고 수동 재시도 버튼을 제공한다. `ERROR`도 내부 예외와
stack trace를 표시하지 않는다. 새 대화는 브라우저 메모리의 현재 `session_id`와 메시지를 초기화한다.

## 계산값과 설명의 경계

결과 카드 숫자는 `facts`만 사용한다. `presentation.explanation`이나 `presentation.next_action`에서
숫자를 파싱하지 않는다. 각 fact의 `source`와 `source_ref`는 기본 화면에서 숨기며, 필요하면
API 응답에서 확인한다. `?debug=1`에서는 민감하지 않은 `execution_id`와 `recommendation_id`만
추가로 표시한다.

합성 데이터 warning과 근거 품질 고지는 결과 카드 하단의 `데이터와 가정` 영역에 표시한다.
페이지 footer에도 프로토타입이 실제 매장 성과를 보증하지 않는다는 고정 고지를 둔다.

## 브라우저 경계

웹사이트 JavaScript에는 API key, System Prompt, ToolResult 원문을 넣거나 표시하지 않는다.
서버가 `ERROR`를 반환하거나 네트워크 요청이 실패해도 브라우저에는 정해진 일반 안내만 표시한다.
대화 중에는 서버가 반환한 같은 `session_id`를 후속 요청에 사용한다.

## 프론트 테스트

채팅 세션과 상태 전이는 외부 의존성 없는 `chat-core.js`에 두었다.

```powershell
node --test tests/test_web_chat.js
```

## 현재 한계

- 합성 데이터로 학습한 데모 계산 결과다.
- 현재 Agent UI는 검증된 가격 질문 흐름만 지원한다.
- 로그인, 브라우저 새로고침 뒤 세션 복원과 장기 대화 저장은 포함하지 않았다.
- HTTP 서버는 해커톤 MVP의 단일 프로세스·단일 worker 전제다.
- 날씨, 세트 구성과 실험 피드백 Agent UI는 후속 범위다.
