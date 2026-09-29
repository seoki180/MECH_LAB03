"""Windows 콘솔 환경에서 진단 출력이 앱을 죽이지 않는지 확인한다.

실제 실패: Windows 러너에서 MECHLab.exe --print-paths가 cp1252 콘솔에 한글을
출력하려다 UnicodeEncodeError로 죽었다. macOS/리눅스는 기본이 UTF-8이라
그냥 실행하면 재현되지 않으므로, 출력 스트림을 바꿔 조건을 강제로 만든다.

실행: uv run python tests/console_encoding_check.py
"""
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from experiment_app.bootstrap import emit, safe_print  # noqa: E402

results = []


def check(condition, message):
    results.append((bool(condition), message))


KOREAN = "사용할 map_pack: D:\\지도\\maps · 데모 · 시험목록 A"


def with_stdout(stream, fn):
    original = sys.stdout
    sys.stdout = stream
    try:
        return fn()
    finally:
        sys.stdout = original


# 1. cp1252 콘솔: 예전 코드는 여기서 UnicodeEncodeError로 죽었다.
cp1252 = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
try:
    with_stdout(cp1252, lambda: safe_print(KOREAN))
    check(True, "cp1252 콘솔에서 예외 없이 반환")
except Exception as error:
    check(False, f"cp1252 콘솔에서 예외 발생: {type(error).__name__}: {error}")

# 2. windowed 빌드: sys.stdout이 None이다.
try:
    with_stdout(None, lambda: safe_print(KOREAN))
    check(True, "stdout이 None이어도 예외 없이 반환")
except Exception as error:
    check(False, f"stdout None에서 예외 발생: {type(error).__name__}: {error}")

# 3. 콘솔이 죽어도 --report 파일은 온전한 UTF-8로 남아야 한다.
with tempfile.TemporaryDirectory() as directory:
    report = Path(directory) / "check.txt"
    broken = io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")
    try:
        with_stdout(broken, lambda: emit(KOREAN, report))
        check(True, "cp1252 콘솔에서 emit이 예외 없이 반환")
    except Exception as error:
        check(False, f"emit이 예외 발생: {type(error).__name__}: {error}")
    check(report.exists(), "보고 파일이 생성됨")
    if report.exists():
        check(report.read_text(encoding="utf-8").strip() == KOREAN,
              "보고 파일 내용이 한글 그대로 보존됨")

# 4. UTF-8 콘솔에서는 원문 그대로 나와야 한다.
utf8 = io.TextIOWrapper(io.BytesIO(), encoding="utf-8", errors="strict")
with_stdout(utf8, lambda: safe_print(KOREAN))
utf8.flush()
check(utf8.buffer.getvalue().decode("utf-8").strip() == KOREAN,
      "UTF-8 콘솔에서는 원문 그대로 출력")

for ok, message in results:
    print(("  OK  " if ok else "  실패 ") + message)
failed = [m for ok, m in results if not ok]
print(f"\n실패 {len(failed)}건" if failed else f"\n{len(results)}건 모두 통과")
sys.exit(1 if failed else 0)
