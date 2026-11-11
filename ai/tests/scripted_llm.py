"""대본대로 답하는 가짜 LLM. 에이전트 테스트가 함께 쓴다."""

import asyncio
import json
from types import SimpleNamespace

HANG = object()


def scripted_client(*turns):
    """턴마다 정해 둔 대로 답하는 가짜 LLM.

    턴은 [(도구 이름, 인자)] 목록, 올릴 예외, HANG(끝나지 않음) 중 하나다. 대본이 끝나면 호출 없이 답한다.
    """
    requests = []
    client = SimpleNamespace(requests=requests, hanging=asyncio.Event(), hang_reaped=False)

    async def create(**request):
        requests.append({**request, "messages": list(request["messages"])})
        turn = turns[len(requests) - 1] if len(requests) <= len(turns) else []
        if turn is HANG:
            client.hanging.set()
            try:
                await asyncio.Event().wait()
            finally:
                client.hang_reaped = True
        if isinstance(turn, BaseException):
            raise turn
        calls = [
            SimpleNamespace(id=f"call-{len(requests)}-{index}", function=SimpleNamespace(
                name=name, arguments=arguments if isinstance(arguments, str) else json.dumps(arguments),
            ))
            for index, (name, arguments) in enumerate(turn)
        ]
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="위험한 문자입니다", tool_calls=calls or None),
        )])

    client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
    return client, requests
