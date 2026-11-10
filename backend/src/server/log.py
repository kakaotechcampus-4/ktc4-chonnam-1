"""서버 진단 로그. `print()` 대신 쓴다.

uvicorn 은 앱 로거에 핸들러를 달지 않아 INFO 줄이 사라진다. 그래서 핸들러를 직접 단다.
줄 모양은 예전 `print()` 출력과 같다(태그와 값만). 문자 원문·전체 주소·토큰은 남기지 않는다.
"""

import logging
import sys


class _StdoutHandler(logging.StreamHandler):
    """기록하는 시점의 `sys.stdout` 에 쓴다.

    만들 때의 stdout 을 붙잡아 두면 테스트(capsys)나 재지정된 stdout 을 따라가지 못한다.
    """

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self):
        return sys.stdout


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = _StdoutHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger
