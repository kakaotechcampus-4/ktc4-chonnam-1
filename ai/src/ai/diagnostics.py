"""실패 로그에 쓸 도우미."""

from pathlib import Path


def where(error: BaseException) -> str:
    """예외 종류와 난 자리(`TypeError at verify.py:281`).

    예외 문구는 넣지 않는다. 문자 원문·대본·서명된 주소가 섞여 있을 수 있다.
    종류만 남기면 코드 실수인지 바깥 실패인지, 어디서 났는지 로그로 알 수 없어서 자리를 함께 남긴다.
    """
    frame = error.__traceback__
    while frame is not None and frame.tb_next is not None:
        frame = frame.tb_next
    if frame is None:
        return type(error).__name__
    return f"{type(error).__name__} at {Path(frame.tb_frame.f_code.co_filename).name}:{frame.tb_lineno}"
