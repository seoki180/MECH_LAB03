"""PyInstaller 진입 스크립트.

`src/experiment_app/__main__.py`는 `python -m experiment_app`으로 실행되는 것을
전제로 상대 import(`from .bootstrap import ...`)를 쓴다. PyInstaller는 진입 스크립트를
패키지가 아닌 최상위 `__main__` 모듈로 실행하므로 상대 import가 깨진다.
그래서 절대 import만 쓰는 이 파일을 빌드 진입점으로 둔다.
"""
from experiment_app.bootstrap import main

if __name__ == "__main__":
    main()
