#!/bin/bash
# 노트북의 저장소에서 실행한다. EC2 로 올리기 전에 메인 서버와 같은 수집 코드를 복사한다.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
install -d "$here/scanner"
cp "$root/scanner/fetch.py" "$root/scanner/models.py" "$here/scanner/"
: > "$here/scanner/__init__.py"
sha256sum "$here/scanner/"*.py "$here/collector/"*.py
