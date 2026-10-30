"""평가용 임베딩 캐시.

같은 문장을 다시 임베딩하지 않게 저장소 밖 파일에 둔다. 재배포 금지 대본의 벡터도 들어가므로
저장소 안에 두지 않는다. 키는 모델 이름과 문장의 sha256 이다.
"""

import hashlib
import json
import os
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from ai.kb.embedding import embed_texts, resolve_model

DEFAULT_DIR = Path(os.getenv("RAG_EMBED_CACHE", Path.home() / ".cache" / "ktc4-rag"))
REQUEST_TIMEOUT_S = 60.0
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """평가 실행용. 저장소 최상위 env 파일의 KEY=VALUE 를 이미 없는 환경변수로만 올린다. 값은 출력하지 않는다."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip().strip("'\"")
        if key.strip() and value and not os.getenv(key.strip()):
            os.environ[key.strip()] = value


load_env_file()


def _key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class EmbeddingCache:
    def __init__(self, model: str | None = None, directory: Path = DEFAULT_DIR) -> None:
        self.model = model or resolve_model()
        self.path = Path(directory) / f"{self.model}.jsonl"
        self.vectors: dict[str, np.ndarray] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    self.vectors[item["k"]] = np.asarray(item["v"], dtype=np.float32)

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """texts 순서대로 단위 벡터 행렬을 돌려준다. 캐시에 없는 문장만 API 로 부른다."""
        missing = list(dict.fromkeys(text for text in texts if _key(text) not in self.vectors))
        if missing:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as out:
                for start in range(0, len(missing), 64):
                    batch = missing[start : start + 64]
                    for text, vector in zip(batch, embed_texts(batch, timeout_s=REQUEST_TIMEOUT_S)):
                        key = _key(text)
                        self.vectors[key] = np.asarray(vector, dtype=np.float32)
                        out.write(json.dumps({"k": key, "v": [round(x, 6) for x in vector]}) + "\n")
        return np.stack([self.vectors[_key(text)] for text in texts]) if texts else np.zeros((0, 0))
